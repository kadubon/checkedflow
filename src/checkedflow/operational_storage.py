"""Atomic local control-state, block and epoch storage; consensus ordering is external."""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from checkedflow.core.key_registry import roots
from checkedflow.core.operational import State
from checkedflow.core.request_journal import Archive
from checkedflow.core.values import (
    JSON,
    Failure,
    Object,
    array,
    fields,
    integer,
    obj,
    require,
    text,
)
from checkedflow.core.work_budget import Ledger
from checkedflow.operational_codec import archive_bytes, decode, decode_archive, state_bytes
from checkedflow.operational_runtime import Runtime
from checkedflow.wire import MAX_TRANSACTION_BYTES, document, dumps

MAX_BLOCK_BYTES = 2097152
MAX_TRANSACTIONS = 256


@dataclass(frozen=True)
class Commit:
    height: int
    state_hash: str
    outcomes: tuple[str, ...]


def _inputs(transactions: Sequence[bytes]) -> tuple[bytes, ...]:
    require(len(transactions) <= MAX_TRANSACTIONS, "LIMIT", "block transaction count")
    for raw in transactions:
        require(isinstance(raw, bytes), "SHAPE", "original transaction bytes required")
        require(len(raw) <= MAX_TRANSACTION_BYTES, "LIMIT", "transaction byte ceiling")
    require(sum(map(len, transactions)) <= MAX_BLOCK_BYTES, "LIMIT", "block byte ceiling")
    return tuple(transactions)


def _block(raw: bytes, expected_hash: str) -> Object:
    require(len(raw) <= 2 * MAX_BLOCK_BYTES + 131072, "LIMIT", "stored block byte ceiling")
    require(sha256(raw).hexdigest() == expected_hash, "STORAGE", "block digest mismatch")
    value = document(raw, string_limit=2 * MAX_TRANSACTION_BYTES)
    fields(value, "height previous_hash state_hash transactions")
    integer(value["height"], low=1)
    return value


def _transactions(block: Object) -> tuple[tuple[bytes, ...], tuple[str, ...]]:
    raws, codes = [], []
    for item in array(block["transactions"], limit=MAX_TRANSACTIONS):
        row = obj(item)
        fields(row, "raw code")
        raw = row["raw"]
        require(
            isinstance(raw, str)
            and len(raw) % 2 == 0
            and all(c in "0123456789abcdef" for c in raw),
            "STORAGE",
            "transaction hex encoding",
        )
        raws.append(bytes.fromhex(cast(str, raw)))
        codes.append(text(row["code"]))
    return _inputs(raws), tuple(codes)


class Store:
    """Use an operator-owned private directory. Never open candidate-supplied SQLite files."""

    def __init__(self, path: Path, initial: State) -> None:
        initial_raw = state_bytes(initial)
        require(decode(initial_raw) == initial, "GENESIS", "invalid initial state")
        require(
            initial.height == 0
            and initial.journal.epoch == 0
            and initial.mode == "paused"
            and not initial.journal.receipts
            and initial.budget == Ledger()
            and not initial.tasks
            and all(item.revision == 1 and item.usable_at(0) for item in initial.credentials)
            and all(nonce == 0 for _, nonce in initial.journal.actors),
            "GENESIS",
            "a fresh genesis is required",
        )
        self.initial = initial
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._connect() as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            require(
                not tables or tables == {"identity", "head", "blocks", "epochs"},
                "VERSION",
                "store profile",
            )
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1), "
                "genesis BLOB NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS head (id INTEGER PRIMARY KEY CHECK(id=1), "
                "height INTEGER NOT NULL, body BLOB NOT NULL, hash TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS blocks (height INTEGER PRIMARY KEY, "
                "body BLOB NOT NULL, hash TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS epochs (epoch INTEGER PRIMARY KEY, "
                "root TEXT UNIQUE NOT NULL, body BLOB NOT NULL)"
            )
            prior = db.execute("SELECT genesis FROM identity WHERE id=1").fetchone()
            if prior is None:
                require(not tables, "STORAGE", "existing store has no identity")
                db.execute("INSERT INTO identity VALUES (1, ?)", (initial_raw,))
                db.execute(
                    "INSERT INTO head VALUES (1, 0, ?, ?)",
                    (initial_raw, sha256(initial_raw).hexdigest()),
                )
            else:
                require(prior[0] == initial_raw, "GENESIS", "store belongs to a different genesis")
                self._head(db)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def _head(self, db: sqlite3.Connection) -> tuple[State, str]:
        row = db.execute("SELECT height, body, hash FROM head WHERE id=1").fetchone()
        require(row is not None, "STORAGE", "missing committed state")
        raw, fingerprint = row[1], row[2]
        require(sha256(raw).hexdigest() == fingerprint, "STORAGE", "state digest mismatch")
        state = decode(raw)
        require(
            state_bytes(state) == raw and state.height == row[0],
            "STORAGE",
            "state encoding or height",
        )
        require(
            (
                state.chain,
                state.mission,
                state.organizations,
                roots(state.credentials),
                state.journal.limits,
            )
            == (
                self.initial.chain,
                self.initial.mission,
                self.initial.organizations,
                roots(self.initial.credentials),
                self.initial.journal.limits,
            ),
            "GENESIS",
            "immutable control configuration differs",
        )
        if state.journal.epoch:
            epoch = db.execute(
                "SELECT root, body FROM epochs WHERE epoch=?", (state.journal.epoch - 1,)
            ).fetchone()
            require(
                epoch is not None and epoch[0] == state.journal.archive_root,
                "STORAGE",
                "missing current epoch archive",
            )
            archive = decode_archive(epoch[1], state.journal.archive_root)
            require(archive.epoch == state.journal.epoch - 1, "STORAGE", "archive epoch differs")
        return state, str(fingerprint)

    def load(self) -> State:
        with self._connect() as db:
            db.execute("BEGIN")
            return self._head(db)[0]

    def commit_block(
        self, height: int, transactions: Sequence[bytes], *, previous_hash: str
    ) -> Commit:
        """Derive state from authenticated bytes; commit archives and state in one transaction."""
        integer(height, low=1)
        inputs = _inputs(transactions)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state, current_hash = self._head(db)
            if height == state.height:
                prior = db.execute(
                    "SELECT body, hash FROM blocks WHERE height=?", (height,)
                ).fetchone()
                require(prior is not None, "STORAGE", "missing repeated block")
                prior_block = _block(prior[0], prior[1])
                old_inputs, old_outcomes = _transactions(prior_block)
                require(
                    old_inputs == inputs
                    and prior_block["previous_hash"] == previous_hash
                    and prior_block["state_hash"] == current_hash,
                    "CONFLICT",
                    "conflicting repeated block",
                )
                return Commit(height, current_hash, old_outcomes)
            require(height == state.height + 1, "HEIGHT", "blocks must be contiguous")
            require(previous_hash == current_hash, "CONFLICT", "stale previous state")
            runtime = Runtime(state)
            runtime.tick(height)
            records: list[JSON] = []
            outcomes = []
            for raw in inputs:
                try:
                    archive = runtime.apply(raw, height=height)
                    code = "OK"
                except Failure as exc:
                    archive = None
                    code = exc.code
                if archive is not None:
                    db.execute(
                        "INSERT INTO epochs VALUES (?, ?, ?)",
                        (archive.epoch, archive.root, archive_bytes(archive)),
                    )
                records.append({"raw": raw.hex(), "code": code})
                outcomes.append(code)
            fingerprint = runtime.state_hash
            block = dumps(
                {
                    "height": height,
                    "previous_hash": current_hash,
                    "state_hash": fingerprint,
                    "transactions": records,
                },
                string_limit=2 * MAX_TRANSACTION_BYTES,
            )
            db.execute(
                "INSERT INTO blocks VALUES (?, ?, ?)", (height, block, sha256(block).hexdigest())
            )
            db.execute(
                "UPDATE head SET height=?, body=?, hash=? WHERE id=1",
                (height, state_bytes(runtime.state), fingerprint),
            )
            return Commit(height, fingerprint, tuple(outcomes))

    def archive(self, epoch: int, *, expected_root: str) -> Archive:
        """The caller obtains expected_root from an already trusted checkpoint, not this reply."""
        integer(epoch)
        with self._connect() as db:
            row = db.execute("SELECT body FROM epochs WHERE epoch=?", (epoch,)).fetchone()
            require(row is not None, "UNAVAILABLE", "archive unavailable")
            archive = decode_archive(row[0], expected_root)
            require(archive.epoch == epoch, "STORAGE", "archive epoch differs")
            return archive

    def verify_history(self, *, expected_hash: str) -> State:
        """Stream signed local history against a separately trusted final application hash."""
        runtime = Runtime(self.initial)
        with self._connect() as db:
            db.execute("BEGIN")
            head, head_hash = self._head(db)
            require(head_hash == expected_hash, "STORAGE", "trusted checkpoint differs")
            for row in db.execute("SELECT height, body, hash FROM blocks ORDER BY height"):
                block = _block(row[1], row[2])
                require(
                    row[0] == block["height"] == runtime.state.height + 1, "HEIGHT", "history gap"
                )
                require(
                    block["previous_hash"] == runtime.state_hash,
                    "STORAGE",
                    "history predecessor differs",
                )
                inputs, expected = _transactions(block)
                runtime.tick(row[0])
                for raw, outcome in zip(inputs, expected, strict=True):
                    try:
                        archive = runtime.apply(raw, height=row[0])
                        code = "OK"
                    except Failure as exc:
                        archive, code = None, exc.code
                    require(code == outcome, "REPLAY", "recorded outcome differs")
                    if archive is not None:
                        stored = db.execute(
                            "SELECT body FROM epochs WHERE epoch=?", (archive.epoch,)
                        ).fetchone()
                        require(stored is not None, "UNAVAILABLE", "historical archive unavailable")
                        require(
                            decode_archive(stored[0], archive.root) == archive,
                            "REPLAY",
                            "archive differs",
                        )
                require(runtime.state_hash == block["state_hash"], "REPLAY", "block state differs")
            require(runtime.state == head, "REPLAY", "final state differs")
        return runtime.state
