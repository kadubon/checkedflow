"""Single-controller retention over a private provider namespace.

Pins survive crashes. Plans bind a catalog revision; tombstones commit before physical erasure.
Current trusted policy and recovery watermarks are supplied by the owning service, never clients.
This catalog is outside consensus and cannot infer live dependencies from artifact contents.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from checkedflow.artifact_io import Access, ErasableStore, read_verified, verify
from checkedflow.core.artifact import Reference, reference
from checkedflow.core.values import (
    MAX_INT,
    Failure,
    Object,
    array,
    fields,
    integer,
    obj,
    require,
    text,
)
from checkedflow.wire import document, dumps


@dataclass(frozen=True)
class Pin:
    scope: str
    identity: str
    sequence: int
    principal: str

    def __post_init__(self) -> None:
        text(self.scope, limit=80)
        text(self.identity, limit=80)
        text(self.principal, limit=80)
        integer(self.sequence, low=1)


@dataclass(frozen=True)
class Plan:
    namespace: str
    scope: str
    revision: int
    objects: tuple[Reference, ...]

    def __post_init__(self) -> None:
        text(self.namespace, limit=128)
        text(self.scope, limit=80)
        require(
            all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in self.scope),
            "SCOPE",
            "portable plan scope required",
        )
        integer(self.revision, low=1)
        require(len(self.objects) <= 128, "LIMIT", "bounded erasure plan")
        require(
            all(ref.scope == self.scope for ref in self.objects)
            and len({ref.digest for ref in self.objects}) == len(self.objects),
            "SCOPE",
            "distinct scope-bound object references required",
        )

    def record(self) -> Object:
        return {
            "version": "checkedflow/retention-plan/v1",
            "namespace": self.namespace,
            "scope": self.scope,
            "revision": self.revision,
            "objects": [ref.record() for ref in self.objects],
        }


def decode_plan(raw: bytes) -> Plan:
    require(len(raw) <= 131_072, "LIMIT", "retention plan byte ceiling")
    record = document(raw)
    fields(record, "version namespace scope revision objects")
    require(
        record["version"] == "checkedflow/retention-plan/v1", "VERSION", "retention plan version"
    )
    return Plan(
        text(record["namespace"], limit=128),
        text(record["scope"], limit=80),
        integer(record["revision"], low=1),
        tuple(reference(obj(item)) for item in array(record["objects"], limit=128)),
    )


@dataclass(frozen=True)
class Erasure:
    digest: str
    status: str


class RetentionStore:
    """Indexed durable catalog; all users of the provider namespace must use this controller.

    trusted_floor must come from an independently retained current recovery watermark. Zero is
    accepted only for creation, never reopening. A stale supplied floor cannot prevent rollback
    beyond that floor. The operational backup layer must bind the actual current catalog revision.
    """

    def __init__(
        self,
        path: Path,
        provider: ErasableStore,
        *,
        namespace: str,
        scope: str,
        trusted_floor: int,
        retention_blocks: int = 100,
        grace_blocks: int = 100,
        object_limit: int = 4096,
        byte_limit: int = 268_435_456,
    ) -> None:
        text(namespace, limit=128)
        text(scope, limit=80)
        require(
            all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in scope),
            "SCOPE",
            "portable scope required",
        )
        integer(trusted_floor)
        integer(retention_blocks, low=1, high=1_000_000)
        integer(grace_blocks, low=1, high=1_000_000)
        integer(object_limit, low=1, high=1_000_000)
        integer(byte_limit, low=1, high=1_099_511_627_776)
        self.path, self.provider = path, provider
        self.namespace, self.scope = namespace, scope
        self.retention_blocks, self.grace_blocks = retention_blocks, grace_blocks
        self.object_limit, self.byte_limit = object_limit, byte_limit
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with self._connect() as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            expected_tables = {
                "retention_identity",
                "retention_objects",
                "retention_pins",
                "retention_roots",
            }
            require(not tables or tables == expected_tables, "VERSION", "retention catalog profile")
            require(
                bool(tables) or trusted_floor == 0, "RESTORE", "catalog absent below trusted floor"
            )
            require(
                not tables or trusted_floor > 0, "RESTORE", "reopening requires a trusted floor"
            )
            if tables:
                columns = tuple(
                    row[1] for row in db.execute("PRAGMA table_info(retention_identity)")
                )
                require(
                    columns
                    == (
                        "id",
                        "profile",
                        "namespace",
                        "scope",
                        "retention",
                        "grace",
                        "object_limit",
                        "byte_limit",
                        "revision",
                        "height",
                        "objects",
                        "bytes",
                    ),
                    "VERSION",
                    "retention catalog schema differs",
                )
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS retention_identity (id INTEGER PRIMARY KEY "
                "CHECK(id=1), "
                "profile TEXT NOT NULL, namespace TEXT NOT NULL, scope TEXT NOT NULL, "
                "retention INTEGER NOT NULL, "
                "grace INTEGER NOT NULL, object_limit INTEGER NOT NULL, byte_limit "
                "INTEGER NOT NULL, "
                "revision INTEGER NOT NULL, height INTEGER NOT NULL, objects INTEGER "
                "NOT NULL, bytes INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS retention_objects (digest TEXT PRIMARY "
                "KEY, reference BLOB NOT NULL, "
                "length INTEGER NOT NULL, until_height INTEGER NOT NULL, status TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS retention_status ON "
                "retention_objects(status, until_height, digest)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS retention_pins (identity TEXT PRIMARY KEY, "
                "sequence INTEGER NOT NULL, "
                "principal TEXT NOT NULL, category TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS retention_roots (pin TEXT NOT NULL "
                "REFERENCES retention_pins(identity) "
                "ON DELETE CASCADE, digest TEXT NOT NULL REFERENCES "
                "retention_objects(digest), PRIMARY KEY(pin,digest))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS retention_root_digest ON retention_roots(digest)"
            )
            expected = (
                "checkedflow/retention-catalog/v1",
                namespace,
                scope,
                retention_blocks,
                grace_blocks,
                object_limit,
                byte_limit,
            )
            prior = db.execute(
                "SELECT profile,namespace,scope,retention,grace,object_limit,byte_limit FROM "
                "retention_identity WHERE id=1"
            ).fetchone()
            if prior is None:
                require(not tables, "STORAGE", "catalog identity missing")
                db.execute(
                    "INSERT INTO retention_identity VALUES(1,?,?,?,?,?,?,?,1,0,0,0)", expected
                )
            else:
                require(prior[0] == expected[0], "VERSION", "retention catalog version differs")
                require(prior == expected, "CONFLICT", "catalog configuration changed")
            require(
                self._meta(db)[0] >= trusted_floor, "RESTORE", "catalog predates trusted watermark"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _meta(db: sqlite3.Connection) -> tuple[int, int, int, int]:
        row = db.execute(
            "SELECT revision,height,objects,bytes FROM retention_identity WHERE id=1"
        ).fetchone()
        require(row is not None, "STORAGE", "catalog identity missing")
        return integer(row[0]), integer(row[1]), integer(row[2]), integer(row[3])

    @staticmethod
    def _bump(db: sqlite3.Connection) -> int:
        revision = RetentionStore._meta(db)[0]
        require(revision < MAX_INT, "CAPACITY", "catalog revision exhausted")
        db.execute("UPDATE retention_identity SET revision=revision+1 WHERE id=1")
        return revision + 1

    def _authorize(self, access: Access, permission: str, ref: Reference | None = None) -> None:
        access.authorize(self.scope, permission)
        require(ref is None or ref.scope == self.scope, "SCOPE", "catalog scope mismatch")

    @staticmethod
    def _live(db: sqlite3.Connection, ref: Reference) -> None:
        row = db.execute(
            "SELECT length,status FROM retention_objects WHERE digest=?", (ref.digest,)
        ).fetchone()
        require(row is not None, "UNAVAILABLE", "artifact is not registered")
        require(row[1] == "live", "RETIRED_ARTIFACT", "artifact has a durable tombstone")
        require(row[0] == ref.length, "INTEGRITY", "catalog length differs")

    def revision(self, *, access: Access) -> int:
        self._authorize(access, "read")
        with self._connect() as db:
            return self._meta(db)[0]

    def advance(self, height: int, *, access: Access) -> None:
        """Caller supplies authenticated monotonic committed height, never candidate time."""
        self._authorize(access, "maintain")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            integer(height, low=self._meta(db)[1], high=MAX_INT - 2_000_000)
            db.execute("UPDATE retention_identity SET height=? WHERE id=1", (height,))
            self._bump(db)

    def put(self, ref: Reference, source: BinaryIO, *, access: Access) -> None:
        self._authorize(access, "write", ref)
        body = read_verified(ref, source)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            _, height, objects, size = self._meta(db)
            exists = db.execute(
                "SELECT 1 FROM retention_objects WHERE digest=?", (ref.digest,)
            ).fetchone()
            if exists:
                self._live(db, ref)
            else:
                require(
                    objects < self.object_limit and size + ref.length <= self.byte_limit,
                    "CAPACITY",
                    "artifact storage quota",
                )
                db.execute(
                    "INSERT INTO retention_objects VALUES(?,?,?,?, 'live')",
                    (ref.digest, dumps(ref.record()), ref.length, height + self.retention_blocks),
                )
                db.execute(
                    "UPDATE retention_identity SET objects=objects+1,bytes=bytes+? WHERE id=1",
                    (ref.length,),
                )
            # Exclude this controller's erasure and admission during provider I/O.
            self.provider.put(ref, BytesIO(body), access=access)
            db.execute(
                "UPDATE retention_objects SET until_height=max(until_height,?) WHERE digest=?",
                (height + self.retention_blocks, ref.digest),
            )
            self._bump(db)

    def get(self, ref: Reference, *, access: Access) -> bytes:
        self._authorize(access, "read", ref)
        with self._connect() as db:
            # WAL readers alone do not block deletion; reserve the writer during verified retrieval.
            db.execute("BEGIN IMMEDIATE")
            self._live(db, ref)
            body = self.provider.get(ref, access=access)
            verify(ref, body)
            return body

    def pin(
        self, identity: str, refs: tuple[Reference, ...], *, category: str, access: Access
    ) -> Pin:
        self._authorize(access, "pin")
        text(identity, limit=80)
        require(
            category in {"pending", "dependency", "effect", "snapshot", "replay"},
            "SHAPE",
            "retention root category",
        )
        require(0 < len(refs) <= 128, "LIMIT", "bounded nonempty pin required")
        for ref in refs:
            self._authorize(access, "pin", ref)
        digests = tuple(sorted({ref.digest for ref in refs}))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for ref in refs:
                self._live(db, ref)
            prior = db.execute(
                "SELECT sequence,principal,category FROM retention_pins WHERE identity=?",
                (identity,),
            ).fetchone()
            if prior:
                existing = tuple(
                    row[0]
                    for row in db.execute(
                        "SELECT digest FROM retention_roots WHERE pin=? ORDER BY digest",
                        (identity,),
                    )
                )
                require(
                    prior[1:] == (access.principal, category) and existing == digests,
                    "CONFLICT",
                    "pin identity already bound",
                )
                return Pin(self.scope, identity, integer(prior[0]), access.principal)
            require(
                db.execute("SELECT count(*) FROM retention_pins").fetchone()[0] < 4096,
                "CAPACITY",
                "retention pin capacity",
            )
            sequence = self._bump(db)
            db.execute(
                "INSERT INTO retention_pins VALUES(?,?,?,?)",
                (identity, sequence, access.principal, category),
            )
            db.executemany(
                "INSERT INTO retention_roots VALUES(?,?)",
                ((identity, digest) for digest in digests),
            )
            return Pin(self.scope, identity, sequence, access.principal)

    def verify_pin(self, pin: Pin, refs: tuple[Reference, ...], *, access: Access) -> None:
        """Verify an existing root and fresh bytes without recreating a missing pin."""
        self._authorize(access, "read")
        require(
            pin.scope == self.scope and pin.principal == access.principal,
            "AUTHORITY",
            "retention pin owner or scope differs",
        )
        require(0 < len(refs) <= 128, "LIMIT", "bounded nonempty pin required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT sequence,principal FROM retention_pins WHERE identity=?", (pin.identity,)
            ).fetchone()
            require(
                row == (pin.sequence, pin.principal), "BINDING", "retention pin missing or changed"
            )
            actual = tuple(
                item[0]
                for item in db.execute(
                    "SELECT digest FROM retention_roots WHERE pin=? ORDER BY digest",
                    (pin.identity,),
                )
            )
            require(
                actual == tuple(sorted({ref.digest for ref in refs})),
                "BINDING",
                "retention root references differ",
            )
            for ref in refs:
                self._authorize(access, "read", ref)
                self._live(db, ref)
                verify(ref, self.provider.get(ref, access=access))

    def release(self, pin: Pin, *, access: Access) -> None:
        self._authorize(access, "pin")
        require(
            pin.scope == self.scope and pin.principal == access.principal,
            "AUTHORITY",
            "pin owner required",
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT sequence,principal FROM retention_pins WHERE identity=?", (pin.identity,)
            ).fetchone()
            require(prior == (pin.sequence, pin.principal), "STALE_PIN", "pin absent or replaced")
            # A newly unprotected object gets a full grace interval, even after a long-lived pin.
            db.execute(
                "UPDATE retention_objects SET until_height=max(until_height,?) WHERE digest IN "
                "(SELECT digest FROM retention_roots WHERE pin=?)",
                (self._meta(db)[1], pin.identity),
            )
            db.execute("DELETE FROM retention_pins WHERE identity=?", (pin.identity,))
            self._bump(db)

    def plan(self, *, access: Access, limit: int = 128) -> Plan:
        """Read-only dry run. No object is marked or erased by planning."""
        self._authorize(access, "maintain")
        integer(limit, low=1, high=128)
        with self._connect() as db:
            db.execute("BEGIN")
            revision, height, _, _ = self._meta(db)
            rows = db.execute(
                "SELECT reference FROM retention_objects o WHERE status='live' AND until_height<=? "
                "AND NOT EXISTS(SELECT 1 FROM retention_roots r WHERE "
                "r.digest=o.digest) ORDER BY digest LIMIT ?",
                (height - self.grace_blocks, limit),
            )
            return Plan(
                self.namespace,
                self.scope,
                revision,
                tuple(reference(document(row[0])) for row in rows),
            )

    def sweep(self, plan: Plan, *, access: Access) -> tuple[Erasure, ...]:
        self._authorize(access, "maintain")
        self._authorize(access, "erase")
        require(
            plan.namespace == self.namespace and plan.scope == self.scope,
            "SCOPE",
            "erasure plan scope",
        )
        require(
            0 < len(plan.objects) <= 128
            and len({ref.digest for ref in plan.objects}) == len(plan.objects),
            "LIMIT",
            "bounded distinct erasure batch",
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision, height, _, _ = self._meta(db)
            require(plan.revision == revision, "STALE_PLAN", "catalog changed after planning")
            for ref in plan.objects:
                self._authorize(access, "erase", ref)
                self._live(db, ref)
                row = db.execute(
                    "SELECT until_height FROM retention_objects WHERE digest=?", (ref.digest,)
                ).fetchone()
                rooted = db.execute(
                    "SELECT 1 FROM retention_roots WHERE digest=? LIMIT 1", (ref.digest,)
                ).fetchone()
                require(
                    row[0] + self.grace_blocks <= height and rooted is None,
                    "RETAINED",
                    "artifact remains protected",
                )
            for ref in plan.objects:
                db.execute(
                    "UPDATE retention_objects SET status='tombstoned' WHERE digest=?", (ref.digest,)
                )
            self._bump(db)
        # Committed tombstones reject new reads/pins/publication before any remote effect.
        return tuple(self.reconcile_erasure(ref, access=access) for ref in plan.objects)

    def reconcile_erasure(self, ref: Reference, *, access: Access) -> Erasure:
        """Explicitly authorized retry of immutable-key erasure, never new publication."""
        self._authorize(access, "maintain", ref)
        self._authorize(access, "erase", ref)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,length FROM retention_objects WHERE digest=?", (ref.digest,)
            ).fetchone()
            require(
                row is not None and row[0] in {"tombstoned", "erased"},
                "RETAINED",
                "committed tombstone required",
            )
            require(row[1] == ref.length, "INTEGRITY", "catalog length differs")
            if row[0] == "erased":
                return Erasure(ref.digest, "erased")
            try:
                self.provider.erase(ref, access=access)
            except (Failure, OSError, sqlite3.Error):
                return Erasure(ref.digest, "unknown")
            db.execute("UPDATE retention_objects SET status='erased' WHERE digest=?", (ref.digest,))
            db.execute(
                "UPDATE retention_identity SET objects=objects-1,bytes=bytes-? WHERE id=1",
                (ref.length,),
            )
            self._bump(db)
            return Erasure(ref.digest, "erased")
