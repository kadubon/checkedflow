"""Single-writer worker nonce coordination with durable original-byte recovery."""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from checkedflow.core.key_registry import roots
from checkedflow.core.operational import State, genesis
from checkedflow.core.request_journal import Receipt
from checkedflow.core.values import Failure, Object, integer, obj, require, text
from checkedflow.core.work_acceptance import VERIFIER_COMMANDS
from checkedflow.core.work_tasks import WORKER_COMMANDS
from checkedflow.operational_codec import decode_archive
from checkedflow.operational_identity import Signer, sign_command
from checkedflow.operational_runtime import Runtime
from checkedflow.wire import digest, document, dumps


class Coordinator:
    """Use one private directory per identity, shared by all local callers, never candidate input.

    Readers must use the operator's validating full node (Client.live_state). This adapter does
    not establish bootstrap trust or continuing quorum, and never runs candidate code. All I/O
    callbacks must have bounded timeouts. No secret is stored in the journal.
    """

    def __init__(
        self,
        directory: Path,
        read: Callable[[], State],
        submit: Callable[[bytes], object],
        signer: Signer,
        *,
        chain: str,
        mission: str,
        actor: str,
        revision: int,
    ) -> None:
        self.chain = text(chain, limit=128)
        self.mission, self.actor = text(mission, limit=80), text(actor, limit=80)
        self.revision = integer(revision, low=1)
        self._read, self._submit, self._signer = read, submit, signer
        self.directory = directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._exclusive() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS coordinator (id INTEGER PRIMARY KEY CHECK(id=1), "
                "binding BLOB NOT NULL, height INTEGER NOT NULL, nonce INTEGER NOT NULL, "
                "pending BLOB, completed BLOB, attempts INTEGER NOT NULL, "
                "state_hash TEXT NOT NULL, outcome TEXT NOT NULL, origin TEXT NOT NULL)"
            )
            require(
                {row[1] for row in db.execute("PRAGMA table_info(coordinator)")}
                == {
                    "id",
                    "binding",
                    "height",
                    "nonce",
                    "pending",
                    "completed",
                    "attempts",
                    "state_hash",
                    "outcome",
                    "origin",
                },
                "VERSION",
                "unsupported worker journal; do not migrate by resetting intent",
            )
            binding = dumps(
                {
                    "version": "checkedflow/worker-journal/v1",
                    "chain": self.chain,
                    "mission": self.mission,
                    "actor": self.actor,
                }
            )
            row = db.execute("SELECT binding FROM coordinator WHERE id=1").fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO coordinator VALUES (1, ?, 0, 0, NULL, NULL, 0, '', '', '')",
                    (binding,),
                )
                db.commit()
            else:
                require(row[0] == binding, "SCOPE", "coordinator belongs to another identity")

    @contextmanager
    def _exclusive(self) -> Iterator[sqlite3.Connection]:
        lock = sqlite3.connect(self.directory / "submission-lock.sqlite", timeout=0)
        db: sqlite3.Connection | None = None
        try:
            try:
                lock.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise Failure("BUSY", "another identity coordinator is active") from exc
            db = sqlite3.connect(self.directory / "submission.sqlite", timeout=0)
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            if db is not None:
                db.close()
            lock.close()

    def _state(self, db: sqlite3.Connection) -> State:
        state = self._read()
        require(
            state.chain == self.chain and state.mission == self.mission,
            "SCOPE",
            "own-node scope differs",
        )
        require(state.profile == "checkedflow/control-state/v2", "VERSION", "own-node profile")
        origin = Runtime(
            genesis(
                state.chain,
                state.mission,
                state.organizations,
                roots(state.credentials),
                limits=state.journal.limits,
            )
        ).state_hash
        pinned = db.execute("SELECT origin FROM coordinator WHERE id=1").fetchone()[0]
        require(not pinned or pinned == origin, "GENESIS", "own-node identity roots changed")
        db.execute("UPDATE coordinator SET origin=? WHERE id=1", (origin,))
        nonce = dict(state.journal.actors).get(self.actor)
        require(nonce is not None, "AUTHORITY", "worker identity missing")
        nonce = integer(nonce)
        height, previous, fingerprint = db.execute(
            "SELECT height, nonce, state_hash FROM coordinator WHERE id=1"
        ).fetchone()
        require(state.height >= height and nonce >= previous, "STALE", "own-node rollback")
        current_hash = Runtime(state).state_hash
        require(
            not fingerprint or state.height != height or fingerprint == current_hash,
            "CONFLICT",
            "own-node state changed at the same height",
        )
        db.execute(
            "UPDATE coordinator SET height=?, nonce=?, state_hash=? WHERE id=1",
            (state.height, nonce, current_hash),
        )
        db.commit()
        return state

    @staticmethod
    def _pending(db: sqlite3.Connection) -> bytes | None:
        row = db.execute("SELECT pending FROM coordinator WHERE id=1").fetchone()
        return None if row[0] is None else bytes(row[0])

    def pending(self) -> bytes | None:
        """Return original signed bytes for local inspection, not permission to execute work."""
        with self._exclusive() as db:
            return self._pending(db)

    def observe(self) -> State:
        """Read the configured own node with persistent rollback floors, without dispatch."""
        with self._exclusive() as db:
            return self._state(db)

    def origin(self) -> str:
        """Bind local execution journals to the configured node's immutable application origin."""
        with self._exclusive() as db:
            self._state(db)
            row = db.execute("SELECT origin FROM coordinator WHERE id=1").fetchone()
            return str(row[0])

    def _confirm(
        self, db: sqlite3.Connection, state: State, raw: bytes, archive: bytes | None = None
    ) -> bool:
        command = obj(document(raw)["command"])
        receipts: tuple[Receipt, ...] = state.journal.receipts
        if archive is not None:
            batch = decode_archive(archive, state.journal.archive_root)
            require(
                batch.epoch == command["epoch"] == state.journal.epoch - 1,
                "EPOCH",
                "only the directly anchored preceding request epoch is accepted",
            )
            receipts = batch.receipts
        elif command["epoch"] != state.journal.epoch:
            return False
        receipt = next((r for r in receipts if r.request == command["id"]), None)
        if receipt is None:
            return False
        require(
            receipt.actor == self.actor
            and receipt.nonce == command["nonce"]
            and receipt.command_digest == digest(command),
            "CONFLICT",
            "committed receipt differs from pending intent",
        )
        db.execute(
            "UPDATE coordinator SET pending=NULL, completed=?, attempts=0, "
            "outcome='confirmed' WHERE id=1",
            (raw,),
        )
        db.commit()
        return True

    def reconcile(self, *, archive: bytes | None = None) -> str:
        """An anchored closed epoch can prove nonmembership; an empty live query cannot."""
        with self._exclusive() as db:
            raw = self._pending(db)
            if raw is None:
                return "idle"
            if self._confirm(db, self._state(db), raw, archive):
                return "confirmed"
            if archive is not None:
                # The complete preceding epoch was checked against the own-node root.
                db.execute(
                    "UPDATE coordinator SET pending=NULL, completed=?, attempts=0, "
                    "outcome='retired_absent' WHERE id=1",
                    (raw,),
                )
                db.commit()
                return "retired_absent"
            return "pending"

    def send(self, request: str, kind: str, payload: Object) -> State:
        """Persist before one send. A second logical request cannot overtake an uncertain one."""
        require(
            kind in WORKER_COMMANDS | VERIFIER_COMMANDS,
            "AUTHORITY",
            "coordinator accepts only worker and verifier commands",
        )
        text(request, limit=80)
        require(payload.get("mission") == self.mission, "SCOPE", "command mission differs")
        with self._exclusive() as db:
            state = self._state(db)
            raw = self._pending(db)
            completed = db.execute("SELECT completed FROM coordinator WHERE id=1").fetchone()[0]
            if raw is None and completed is not None:
                prior = obj(document(completed)["command"])
                if prior["id"] == request:
                    require(
                        prior["kind"] == kind and prior["payload"] == payload,
                        "CONFLICT",
                        "completed request arguments differ",
                    )
                    outcome = db.execute("SELECT outcome FROM coordinator WHERE id=1").fetchone()[0]
                    require(
                        outcome == "confirmed",
                        "RETIRED_REQUEST",
                        "request retired without commitment",
                    )
                    return state
            if raw is not None:
                prior = obj(document(raw)["command"])
                require(
                    prior["id"] == request
                    and prior["kind"] == kind
                    and prior["payload"] == payload,
                    "OUTCOME_UNKNOWN",
                    "reconcile the outstanding request first",
                )
                require(self._confirm(db, state, raw), "OUTCOME_UNKNOWN", "request still uncertain")
                return state
            command: Object = {
                "api_version": "checkedflow/v2",
                "chain": self.chain,
                "epoch": state.journal.epoch,
                "id": request,
                "actor": self.actor,
                "revision": self.revision,
                "nonce": dict(state.journal.actors)[self.actor] + 1,
                "kind": kind,
                "payload": payload,
            }
            raw = sign_command(command, {(self.actor, self.revision): self._signer})
            Runtime(state).apply(raw, height=state.height + 1)
            db.execute("UPDATE coordinator SET pending=?, attempts=0 WHERE id=1", (raw,))
            db.commit()
            return self._dispatch(db, raw)

    def _dispatch(self, db: sqlite3.Connection, raw: bytes) -> State:
        attempts = db.execute("SELECT attempts FROM coordinator WHERE id=1").fetchone()[0]
        require(attempts < 3, "LIMIT", "original command transmission limit reached")
        db.execute("UPDATE coordinator SET attempts=attempts+1 WHERE id=1")
        db.commit()
        try:
            self._submit(raw)
        except Exception:
            # A rejected/malformed/lost response never proves absence of an earlier transmission.
            raise Failure(
                "OUTCOME_UNKNOWN", "original request retained; reconcile before retry"
            ) from None
        state = self._state(db)
        require(self._confirm(db, state, raw), "OUTCOME_UNKNOWN", "committed receipt not observed")
        return state

    def retransmit(self) -> State:
        """Explicitly resend identical bytes, at most three total calls; never execute a task."""
        with self._exclusive() as db:
            raw = self._pending(db)
            require(raw is not None, "NOT_FOUND", "no pending request")
            raw = cast(bytes, raw)
            state = self._state(db)
            if self._confirm(db, state, raw):
                return state
            Runtime(state).apply(raw, height=state.height + 1)
            return self._dispatch(db, raw)
