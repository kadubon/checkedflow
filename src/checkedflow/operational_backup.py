"""Bounded signed-history backups; independently trusted checkpoints remain mandatory.

This restores only the application database. It does not restore CometBFT, signing state,
artifact bytes, provider journals or checkpoint custody, and must not start a validator.
"""

import os
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

from checkedflow.core.operational import State
from checkedflow.core.values import Object, fields, integer, require, text
from checkedflow.core.work_acceptance import fingerprint
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import MAX_BLOCK_BYTES, Store, _block, _transactions
from checkedflow.wire import MAX_TRANSACTION_BYTES, document, dumps

VERSION = "checkedflow/application-backup/v1"
MAX_BYTES = 1_073_741_824
MAX_BLOCKS = 1_000_000
MAX_LINE = 2 * MAX_BLOCK_BYTES + 131073


@dataclass(frozen=True)
class Checkpoint:
    chain: str
    mission: str
    genesis_hash: str
    height: int
    state_hash: str
    digest: str
    length: int

    def __post_init__(self) -> None:
        text(self.chain, limit=128)
        text(self.mission, limit=80)
        for value in (self.genesis_hash, self.state_hash, self.digest):
            fingerprint(value)
        integer(self.height, high=MAX_BLOCKS)
        integer(self.length, low=1, high=MAX_BYTES)

    def record(self) -> Object:
        return {"version": VERSION, **asdict(self)}


def decode_checkpoint(raw: bytes) -> Checkpoint:
    """Parse metadata, without authenticating its authority or freshness."""
    require(len(raw) <= 4096, "LIMIT", "checkpoint byte ceiling")
    row = document(raw)
    fields(row, "version chain mission genesis_hash height state_hash digest length")
    require(row["version"] == VERSION, "VERSION", "application backup profile")
    return Checkpoint(
        text(row["chain"]),
        text(row["mission"]),
        fingerprint(row["genesis_hash"]),
        integer(row["height"]),
        fingerprint(row["state_hash"]),
        fingerprint(row["digest"]),
        integer(row["length"]),
    )


def _header(initial: State) -> Object:
    return {
        "version": VERSION,
        "chain": initial.chain,
        "mission": initial.mission,
        "genesis_hash": Runtime(initial).state_hash,
    }


def export_history(
    store: Store,
    output: BinaryIO,
    *,
    expected_hash: str,
    byte_limit: int = 67_108_864,
    block_limit: int = 100_000,
) -> Checkpoint:
    """Verify and stream one consistent WAL read snapshot, including rejected transactions.

    A failed call may leave partial caller-owned output. Only a returned checkpoint identifies
    a complete export. The caller owns fsync, access control and independent checkpoint custody.
    """
    fingerprint(expected_hash)
    integer(byte_limit, low=1, high=MAX_BYTES)
    integer(block_limit, high=MAX_BLOCKS)
    digest = sha256()
    length = count = 0

    def emit(raw: bytes) -> None:
        nonlocal length, count
        # Preserve original transaction bytes but normalize the unsigned journal container.
        raw = dumps(
            document(raw, string_limit=2 * MAX_TRANSACTION_BYTES),
            string_limit=2 * MAX_TRANSACTION_BYTES,
        )
        line = raw + b"\n"
        require(len(line) <= MAX_LINE, "LIMIT", "backup line ceiling")
        require(length + len(line) <= byte_limit, "LIMIT", "backup byte ceiling")
        require(count <= block_limit, "LIMIT", "backup block ceiling")
        require(output.write(line) == len(line), "STORAGE", "incomplete backup write")
        digest.update(line)
        length += len(line)
        count += 1

    emit(dumps(_header(store.initial)))
    state = store.verify_history(expected_hash=expected_hash, consume=emit)
    return Checkpoint(
        state.chain,
        state.mission,
        Runtime(store.initial).state_hash,
        state.height,
        expected_hash,
        digest.hexdigest(),
        length,
    )


def restore_history(
    source: BinaryIO,
    destination: Path,
    *,
    initial: State,
    checkpoint: Checkpoint,
    current_height: int,
) -> Store:
    """Replay into a new private directory and activate only after every check succeeds.

    Checkpoint and current_height must come from independently protected current operator state.
    Parent paths are trusted operator configuration. Failure preserves the private pending DB;
    the destination cannot be reused or silently substituted for a live deployment.
    """
    integer(current_height)
    require(checkpoint.height >= current_height, "RESTORE", "checkpoint predates current floor")
    require(
        (checkpoint.chain, checkpoint.mission, checkpoint.genesis_hash)
        == (initial.chain, initial.mission, Runtime(initial).state_hash),
        "BINDING",
        "checkpoint genesis or scope differs",
    )
    digest = sha256()
    length = 0

    def read() -> bytes:
        nonlocal length
        raw = source.readline(min(MAX_LINE + 1, checkpoint.length - length + 1))
        require(
            bool(raw) and raw.endswith(b"\n") and len(raw) <= MAX_LINE,
            "SHAPE",
            "bounded complete JSONL record required",
        )
        require(length + len(raw) <= checkpoint.length, "LIMIT", "backup exceeds checkpoint")
        digest.update(raw)
        length += len(raw)
        return raw[:-1]

    header = read()
    require(header == dumps(_header(initial)), "BINDING", "canonical backup header differs")
    destination.mkdir(mode=0o700)
    pending = destination / "pending.sqlite"
    store = Store(pending, initial)
    previous = checkpoint.genesis_hash
    for height in range(1, checkpoint.height + 1):
        raw = read()
        row = _block(raw, sha256(raw).hexdigest())
        require(
            raw == dumps(row, string_limit=2 * MAX_TRANSACTION_BYTES),
            "SHAPE",
            "canonical block required",
        )
        require(row["height"] == height, "HEIGHT", "backup history gap")
        require(row["previous_hash"] == previous, "BINDING", "backup predecessor differs")
        inputs, outcomes = _transactions(row)
        committed = store.commit_block(height, inputs, previous_hash=previous)
        require(
            committed.outcomes == outcomes and committed.state_hash == row["state_hash"],
            "REPLAY",
            "backup replay differs",
        )
        previous = committed.state_hash
    require(source.read(1) == b"", "SHAPE", "trailing backup data")
    require(
        length == checkpoint.length and digest.hexdigest() == checkpoint.digest,
        "BINDING",
        "backup content differs from checkpoint",
    )
    require(previous == checkpoint.state_hash, "BINDING", "final application checkpoint differs")
    with store._connect() as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("PRAGMA journal_mode=DELETE")
    with pending.open("r+b") as stream:
        os.fsync(stream.fileno())
    active = destination / "application.sqlite"
    pending.rename(active)
    return Store(active, initial)
