"""Portable retention-catalog backup and staged, externally anchored restoration.

This is not a consensus snapshot or a trust-discovery protocol. The caller must authenticate the
checkpoint independently, retain the current revision floor, and stop the old controller before
using a restored one. Provider bytes and credentials are deliberately outside this format.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

from checkedflow.artifact_io import Access, ErasableStore
from checkedflow.core.artifact import reference
from checkedflow.core.values import MAX_INT, Object, array, fields, integer, obj, require, text
from checkedflow.retention import RetentionStore
from checkedflow.wire import document, dumps

VERSION = "checkedflow/retention-backup/v1"
MAX_BYTES = 1_073_741_824
MAX_RECORDS = 1_100_000
MAX_LINE = 16_384


@dataclass(frozen=True)
class Checkpoint:
    """Metadata, not self-authenticating authority; no keys are accepted from backup contents."""

    namespace: str
    scope: str
    revision: int
    height: int
    digest: str
    length: int
    records: int

    def __post_init__(self) -> None:
        text(self.namespace, limit=128)
        text(self.scope, limit=80)
        require(
            all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in self.scope),
            "SCOPE",
            "portable backup scope required",
        )
        integer(self.revision, low=1)
        integer(self.height, high=MAX_INT - 2_000_000)
        require(
            len(self.digest) == 64 and all(c in "0123456789abcdef" for c in self.digest),
            "BINDING",
            "backup SHA-256 required",
        )
        integer(self.length, low=1, high=MAX_BYTES)
        integer(self.records, low=1, high=MAX_RECORDS)

    def record(self) -> Object:
        return {
            "version": VERSION,
            "namespace": self.namespace,
            "scope": self.scope,
            "revision": self.revision,
            "height": self.height,
            "digest": self.digest,
            "length": self.length,
            "records": self.records,
        }


def decode_checkpoint(raw: bytes) -> Checkpoint:
    """Parse metadata only. Parsing cannot establish checkpoint provenance or freshness."""
    require(len(raw) <= MAX_LINE, "LIMIT", "checkpoint byte ceiling")
    row = document(raw)
    fields(row, "version namespace scope revision height digest length records")
    require(row["version"] == VERSION, "VERSION", "retention backup profile")
    return Checkpoint(
        text(row["namespace"], limit=128),
        text(row["scope"], limit=80),
        integer(row["revision"], low=1),
        integer(row["height"]),
        text(row["digest"], limit=64),
        integer(row["length"], low=1),
        integer(row["records"], low=1),
    )


def export_catalog(
    store: RetentionStore,
    output: BinaryIO,
    *,
    access: Access,
    byte_limit: int = 67_108_864,
    record_limit: int = 100_000,
) -> Checkpoint:
    """Stream one consistent SQLite read snapshot; never read or erase provider objects.

    Output belongs to the caller and may be partial after failure. Only a returned checkpoint
    identifies a complete export. Publication/fsync and independent checkpoint custody belong
    to the caller. Concurrent later writes are not covered by this snapshot.
    """
    access.authorize(store.scope, "backup")
    integer(byte_limit, low=1, high=MAX_BYTES)
    integer(record_limit, low=1, high=MAX_RECORDS)
    digest = sha256()
    length = count = 0

    def emit(row: Object) -> None:
        nonlocal length, count
        raw = dumps(row) + b"\n"
        require(len(raw) <= MAX_LINE, "LIMIT", "backup record byte ceiling")
        require(length + len(raw) <= byte_limit and count < record_limit, "LIMIT", "backup ceiling")
        require(output.write(raw) == len(raw), "STORAGE", "incomplete backup write")
        digest.update(raw)
        length += len(raw)
        count += 1

    # A read transaction pins identity, objects and roots to the same WAL snapshot.
    with store._connect() as db:
        db.execute("BEGIN")
        revision, height, objects, size = store._meta(db)
        emit(
            {
                "type": "header",
                "version": VERSION,
                "namespace": store.namespace,
                "scope": store.scope,
                "revision": revision,
                "height": height,
                "retention_blocks": store.retention_blocks,
                "grace_blocks": store.grace_blocks,
                "object_limit": store.object_limit,
                "byte_limit": store.byte_limit,
                "objects": objects,
                "bytes": size,
            }
        )
        for body, until, status in db.execute(
            "SELECT reference,until_height,status FROM retention_objects ORDER BY digest"
        ):
            emit({"type": "object", "reference": document(body), "until": until, "status": status})
        for identity, sequence, principal, category in db.execute(
            "SELECT identity,sequence,principal,category FROM retention_pins ORDER BY identity"
        ):
            emit(
                {
                    "type": "pin",
                    "identity": identity,
                    "sequence": sequence,
                    "principal": principal,
                    "category": category,
                    "digests": [
                        row[0]
                        for row in db.execute(
                            "SELECT digest FROM retention_roots WHERE pin=? ORDER BY digest",
                            (identity,),
                        )
                    ],
                }
            )
    return Checkpoint(
        store.namespace, store.scope, revision, height, digest.hexdigest(), length, count
    )


def _records(source: BinaryIO, checkpoint: Checkpoint) -> Iterator[Object]:
    digest = sha256()
    length = count = 0
    while True:
        ceiling = min(MAX_LINE + 1, checkpoint.length - length + 1)
        raw = source.readline(ceiling)
        require(isinstance(raw, bytes), "SHAPE", "binary backup stream required")
        require(len(raw) <= ceiling, "LIMIT", "stream exceeded requested line bound")
        if not raw:
            break
        length += len(raw)
        count += 1
        require(
            length <= checkpoint.length and count <= checkpoint.records, "LIMIT", "backup ceiling"
        )
        require(len(raw) <= MAX_LINE and raw.endswith(b"\n"), "SHAPE", "complete bounded JSON line")
        row = document(raw[:-1])
        require(dumps(row) + b"\n" == raw, "SHAPE", "canonical backup JSON line required")
        digest.update(raw)
        yield row
    require(
        length == checkpoint.length
        and count == checkpoint.records
        and digest.hexdigest() == checkpoint.digest,
        "INTEGRITY",
        "backup differs from independently trusted checkpoint",
    )


def restore_catalog(
    source: BinaryIO,
    destination: Path,
    provider: ErasableStore,
    *,
    checkpoint: Checkpoint,
    current_revision: int,
    namespace: str,
    scope: str,
    access: Access,
) -> RetentionStore:
    """Restore into a NEW private directory; never overwrite an existing controller.

    Checkpoint and current_revision must be obtained independently of source. The destination's
    catalog.sqlite appears only after all records, counters, references and the exact digest pass.
    This function does not stop the old controller or prove provider availability.
    """
    access.authorize(scope, "restore")
    require(
        checkpoint.namespace == namespace and checkpoint.scope == scope,
        "SCOPE",
        "backup controller identity mismatch",
    )
    integer(current_revision, low=1)
    require(checkpoint.revision >= current_revision, "RESTORE", "backup predates current watermark")
    records = _records(source, checkpoint)
    header = next(records, None)
    require(header is not None, "SHAPE", "backup header required")
    header = obj(header)
    fields(
        header,
        "type version namespace scope revision height retention_blocks grace_blocks "
        "object_limit byte_limit objects bytes",
    )
    require(header["type"] == "header" and header["version"] == VERSION, "VERSION", "backup header")
    require(
        header["namespace"] == namespace
        and header["scope"] == scope
        and integer(header["revision"], low=1) == checkpoint.revision
        and integer(header["height"]) == checkpoint.height,
        "BINDING",
        "backup header differs from checkpoint",
    )
    retention = integer(header["retention_blocks"], low=1, high=1_000_000)
    grace = integer(header["grace_blocks"], low=1, high=1_000_000)
    object_limit = integer(header["object_limit"], low=1, high=1_000_000)
    byte_limit = integer(header["byte_limit"], low=1, high=1_099_511_627_776)
    expected_objects = integer(header["objects"], high=object_limit)
    expected_bytes = integer(header["bytes"], high=byte_limit)
    # Parent paths are trusted operator configuration. Exclusive mkdir prevents replacement of a
    # live store, existing directory, or link, including after a concurrent restore wins the race.
    destination.mkdir(mode=0o700)
    staging = destination / "pending.sqlite"
    try:
        store = RetentionStore(
            staging,
            provider,
            namespace=namespace,
            scope=scope,
            trusted_floor=0,
            retention_blocks=retention,
            grace_blocks=grace,
            object_limit=object_limit,
            byte_limit=byte_limit,
        )
        with store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            objects = size = pins = 0
            last_digest = last_pin = ""
            for row in records:
                if row.get("type") == "object":
                    fields(row, "type reference until status")
                    ref = reference(obj(row["reference"]))
                    require(
                        not pins and ref.scope == scope and last_digest < ref.digest,
                        "BINDING",
                        "sorted distinct objects before pins required",
                    )
                    last_digest = ref.digest
                    until = integer(row["until"], high=checkpoint.height + retention)
                    status = text(row["status"])
                    require(status in {"live", "tombstoned", "erased"}, "STATE", "object status")
                    db.execute(
                        "INSERT INTO retention_objects VALUES(?,?,?,?,?)",
                        (ref.digest, dumps(ref.record()), ref.length, until, status),
                    )
                    if status != "erased":
                        objects += 1
                        size += ref.length
                    require(
                        objects <= object_limit and size <= byte_limit, "CAPACITY", "backup quota"
                    )
                else:
                    fields(row, "type identity sequence principal category digests")
                    require(row["type"] == "pin", "VERSION", "backup record type")
                    identity = text(row["identity"], limit=80)
                    require(last_pin < identity and pins < 4096, "STATE", "sorted bounded pins")
                    last_pin = identity
                    pins += 1
                    sequence = integer(row["sequence"], low=1, high=checkpoint.revision)
                    principal = text(row["principal"], limit=80)
                    category = text(row["category"])
                    require(
                        category in {"pending", "dependency", "effect", "snapshot", "replay"},
                        "STATE",
                        "pin category",
                    )
                    digests = tuple(
                        text(value, limit=64) for value in array(row["digests"], limit=128)
                    )
                    require(
                        bool(digests) and tuple(sorted(set(digests))) == digests,
                        "STATE",
                        "sorted distinct nonempty pin roots",
                    )
                    db.execute(
                        "INSERT INTO retention_pins VALUES(?,?,?,?)",
                        (identity, sequence, principal, category),
                    )
                    for digest in digests:
                        status_row = db.execute(
                            "SELECT status FROM retention_objects WHERE digest=?", (digest,)
                        ).fetchone()
                        require(
                            status_row == ("live",), "STATE", "pin requires registered live object"
                        )
                        db.execute("INSERT INTO retention_roots VALUES(?,?)", (identity, digest))
            require(
                (objects, size) == (expected_objects, expected_bytes),
                "STATE",
                "backup accounting mismatch",
            )
            db.execute(
                "UPDATE retention_identity SET revision=?,height=?,objects=?,bytes=? WHERE id=1",
                (checkpoint.revision, checkpoint.height, objects, size),
            )
        # All SQLite connections are closed: the final WAL checkpoint is in the database file.
        # Rename is within the newly owned directory, not across filesystems.
        os.replace(staging, destination / "catalog.sqlite")
    except BaseException:
        # Only this call's exclusive, fixed-name staging files can be removed. An interrupted
        # process may leave the directory for operator inspection; a retry never overwrites it.
        for name in (
            "pending.sqlite-wal",
            "pending.sqlite-shm",
            "pending.sqlite-journal",
            "pending.sqlite",
        ):
            (destination / name).unlink(missing_ok=True)
        destination.rmdir()
        raise
    store.path = destination / "catalog.sqlite"
    return store
