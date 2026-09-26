"""Scoped local content-addressed bytes in a private SQLite filesystem store.

This adapter provides integrity and atomic visibility, not consensus availability or HTTP auth.
Candidate code must never receive the database, an Access instance, or this adapter.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, cast

# Re-export the initial SDK imports while keeping providers independent of each other.
from checkedflow.artifact_io import Access as Access
from checkedflow.artifact_io import ArtifactStore as ArtifactStore
from checkedflow.artifact_io import read_verified, verify
from checkedflow.core.artifact import MAX_ARTIFACT_BYTES, Reference
from checkedflow.core.values import integer, require


class LocalStore:
    """Bounded local backend; SQLite avoids untrusted filenames and atomic-rename races.

    The operator owns the database directory and permissions. Arbitrary supplied database files
    and hostile same-user filesystem writers are outside this local adapter's trust boundary.
    Give erase authority only to the owning retention controller, never to ordinary workers.
    """

    def __init__(
        self,
        path: Path,
        *,
        scope_objects: int = 4096,
        scope_bytes: int = 268_435_456,
    ) -> None:
        integer(scope_objects, low=1, high=1_000_000)
        integer(scope_bytes, low=1, high=1_099_511_627_776)
        self.path = path
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with self._connect() as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            require(
                not tables or tables == {"artifact_identity", "artifact_objects"},
                "VERSION",
                "artifact store profile",
            )
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS artifact_identity "
                "(id INTEGER PRIMARY KEY CHECK(id=1), profile TEXT NOT NULL, "
                "scope_objects INTEGER NOT NULL, scope_bytes INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS artifact_objects "
                "(scope TEXT NOT NULL, digest TEXT NOT NULL, body BLOB NOT NULL, "
                "PRIMARY KEY(scope, digest))"
            )
            expected = ("checkedflow/local-artifacts/v1", scope_objects, scope_bytes)
            prior = db.execute(
                "SELECT profile, scope_objects, scope_bytes FROM artifact_identity WHERE id=1"
            ).fetchone()
            if prior is None:
                require(not tables, "STORAGE", "artifact store identity missing")
                db.execute("INSERT INTO artifact_identity VALUES (1, ?, ?, ?)", expected)
            else:
                require(prior == expected, "CONFLICT", "artifact store configuration differs")
        self.scope_objects = scope_objects
        self.scope_bytes = scope_bytes

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _get(db: sqlite3.Connection, ref: Reference) -> bytes | None:
        row = db.execute(
            "SELECT length(body) FROM artifact_objects WHERE scope=? AND digest=?",
            (ref.scope, ref.digest),
        ).fetchone()
        if row is None:
            return None
        require(row[0] == ref.length <= MAX_ARTIFACT_BYTES, "INTEGRITY", "stored length differs")
        stored = db.execute(
            "SELECT body FROM artifact_objects WHERE scope=? AND digest=?", (ref.scope, ref.digest)
        ).fetchone()
        body: bytes = stored[0]
        require(isinstance(body, bytes), "INTEGRITY", "stored bytes required")
        verify(ref, body)
        return body

    def put(self, ref: Reference, source: BinaryIO, *, access: Access) -> None:
        access.authorize(ref.scope, "write")
        body = read_verified(ref, source)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = self._get(db, ref)
            if prior is not None:
                require(prior == body, "INTEGRITY", "conflicting artifact bytes")
                return
            usage = db.execute(
                "SELECT count(*), coalesce(sum(length(body)), 0) "
                "FROM artifact_objects WHERE scope=?",
                (ref.scope,),
            ).fetchone()
            require(
                usage[0] < self.scope_objects and usage[1] + len(body) <= self.scope_bytes,
                "CAPACITY",
                "artifact scope quota exceeded",
            )
            db.execute(
                "INSERT INTO artifact_objects VALUES (?, ?, ?)", (ref.scope, ref.digest, body)
            )

    def get(self, ref: Reference, *, access: Access) -> bytes:
        access.authorize(ref.scope, "read")
        with self._connect() as db:
            db.execute("BEGIN")
            body = self._get(db, ref)
            require(body is not None, "UNAVAILABLE", "artifact unavailable")
            return cast(bytes, body)

    def erase(self, ref: Reference, *, access: Access) -> None:
        """Privileged physical erasure, separate from retention and verification revocation."""
        access.authorize(ref.scope, "erase")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM artifact_objects WHERE scope=? AND digest=?", (ref.scope, ref.digest)
            )
