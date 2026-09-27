"""Private host journal for independently recovering owned OCI containers.

No candidate code runs here. Docker access, the journal and the host are trusted operator
resources. A successful absence query is distinct from an unavailable daemon.
"""

import argparse
import platform
import re
import shutil
import sqlite3
import subprocess  # nosec B404
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, cast

from checkedflow.core.values import Failure, Object, obj, require
from checkedflow.wire import document

LABEL = "org.checkedflow.recovery"
LOCAL_DOCKER = "--host=unix:///var/run/docker.sock"
MAX_PENDING = 32
FRESH_NS = 3_000_000_000


class Engine(Protocol):
    def identity(self) -> str: ...

    def inspect(self, identity: str) -> Object | None: ...

    def remove(self, identity: str) -> None: ...

    def managed(self) -> tuple[str, ...]: ...


def boot_id() -> str:
    require(platform.system() == "Linux", "SANDBOX_UNAVAILABLE", "Linux recovery required")
    return str(uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))


class Docker:
    """Bounded CLI adapter. Operators must keep its Docker context stable and local."""

    def __init__(self, executable: str = "docker") -> None:
        found = shutil.which(executable)
        require(found is not None, "SANDBOX_UNAVAILABLE", "Docker executable missing")
        self.executable = cast(str, found)

    def _call(self, *args: str) -> bytes:
        try:
            result = subprocess.run(  # nosec B603
                [self.executable, LOCAL_DOCKER, *args], capture_output=True, timeout=10, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            raise Failure("CLEANUP_UNKNOWN", "Docker recovery request unavailable") from None
        require(result.returncode == 0, "CLEANUP_UNKNOWN", "Docker recovery request rejected")
        require(len(result.stdout) <= 1048576, "LIMIT", "Docker recovery response too large")
        return result.stdout

    def identity(self) -> str:
        identity = self._call("info", "--format", "{{.ID}}").decode("ascii").strip()
        require(bool(identity) and len(identity) <= 128, "BINDING", "Docker identity missing")
        return identity

    def inspect(self, identity: str) -> Object | None:
        # Only names generated below or immutable IDs are accepted, never caller filter syntax.
        is_id = re.fullmatch(r"[0-9a-f]{64}", identity) is not None
        require(
            is_id or re.fullmatch(r"checkedflow-[0-9a-f]{32}", identity) is not None,
            "BINDING",
            "invalid recovery identity",
        )
        selector = f"id={identity}" if is_id else f"name=^/{identity}$"
        matches = self._call("ps", "-a", "--no-trunc", "--filter", selector, "--format", "{{.ID}}")
        lines = matches.decode("ascii").split()
        require(len(lines) <= 1, "BINDING", "ambiguous recovery identity")
        if not lines:
            return None
        require(re.fullmatch(r"[0-9a-f]{64}", lines[0]) is not None, "BINDING", "invalid ID")
        return document(self._call("inspect", "--format", "{{json .}}", lines[0]))

    def remove(self, identity: str) -> None:
        require(
            re.fullmatch(r"[0-9a-f]{64}", identity) is not None, "BINDING", "immutable ID required"
        )
        self._call("rm", "--force", identity)

    def managed(self) -> tuple[str, ...]:
        raw = self._call(
            "ps", "-a", "--no-trunc", "--filter", f"label={LABEL}", "--format", "{{.ID}}"
        )
        identities = tuple(raw.decode("ascii").split())
        require(
            len(identities) <= MAX_PENDING, "LIMIT", "managed container inventory exceeds capacity"
        )
        require(
            all(re.fullmatch(r"[0-9a-f]{64}", value) for value in identities),
            "BINDING",
            "invalid managed container ID",
        )
        return identities


class Recovery:
    """Persist a creation intent before Docker and bind its immutable ID before start.

    Unseen creation outcomes remain pinned, even after a successful absence query. They can
    arrive late. Confirmed IDs cannot be recreated by a delayed start after removal.
    """

    def __init__(
        self,
        directory: Path,
        engine: Engine,
        boot: str,
        *,
        clock: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.engine, self.boot, self.clock = engine, str(uuid.UUID(boot)), clock
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "sandbox.sqlite"
        identity = engine.identity()
        with self._db() as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            require(
                not tables or tables == {"owner", "containers"},
                "VERSION",
                "sandbox journal profile",
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS owner (id INTEGER PRIMARY KEY, engine TEXT, "
                "boot TEXT, heartbeat INTEGER)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS containers (name TEXT PRIMARY KEY, token TEXT, "
                "container TEXT, boot TEXT, deadline INTEGER)"
            )
            row = db.execute("SELECT engine FROM owner WHERE id=1").fetchone()
            if row is None:
                require(not tables, "STORAGE", "missing sandbox owner; recovery required")
                db.execute("INSERT INTO owner VALUES (1, ?, '', 0)", (identity,))
            else:
                require(row[0] == identity, "BINDING", "Docker daemon changed")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        finally:
            db.close()

    def _ready(self, db: sqlite3.Connection) -> None:
        boot, heartbeat = db.execute("SELECT boot, heartbeat FROM owner WHERE id=1").fetchone()
        require(
            boot == self.boot and 0 <= self.clock() - heartbeat <= FRESH_NS,
            "SANDBOX_UNAVAILABLE",
            "independent recovery service is not fresh",
        )

    def reserve(self, seconds: int) -> tuple[str, str]:
        require(type(seconds) is int and 1 <= seconds <= 3600, "LIMIT", "recovery deadline")
        with self._db() as db:
            self._ready(db)
            count = db.execute("SELECT COUNT(*) FROM containers").fetchone()[0]
            require(count < MAX_PENDING, "LIMIT", "unresolved container recovery capacity")
            name, token = "checkedflow-" + uuid.uuid4().hex, uuid.uuid4().hex
            db.execute(
                "INSERT INTO containers VALUES (?, ?, '', ?, ?)",
                (name, token, self.boot, self.clock() + seconds * 1_000_000_000),
            )
            return name, token

    def ready(self) -> None:
        """Check a recent successful sweep; this is not a perpetual availability promise."""
        with self._db() as db:
            self._ready(db)

    def _owned(self, record: tuple[str, str, str, str, int]) -> str | None:
        name, token, container, _, _ = record
        info = self.engine.inspect(container or name)
        if info is None:
            return None
        identity = info.get("Id")
        require(
            isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{64}", identity) is not None,
            "BINDING",
            "invalid inspected ID",
        )
        require(
            info.get("Name") == "/" + name
            and obj(obj(info["Config"])["Labels"]).get(LABEL) == token
            and obj(info["HostConfig"]).get("Runtime") == "runsc"
            and (not container or identity == container),
            "BINDING",
            "container ownership differs",
        )
        return cast(str, identity)

    def bind(self, name: str, container: str) -> None:
        with self._db() as db:
            self._ready(db)
            row = db.execute("SELECT * FROM containers WHERE name=?", (name,)).fetchone()
            require(row is not None, "EXPIRED", "container intent already retired")
            require(row[3] == self.boot and self.clock() < row[4], "EXPIRED", "container deadline")
            require(self._owned(row) == container, "BINDING", "created container differs")
            require(self.clock() < row[4], "EXPIRED", "container inspection exceeded deadline")
            db.execute("UPDATE containers SET container=? WHERE name=?", (container, name))

    def _remove(self, db: sqlite3.Connection, record: tuple[str, str, str, str, int]) -> bool:
        identity = self._owned(record)
        if identity is not None:
            # Persist discovery before removal; a crash cannot turn a known ID back into absence.
            db.execute("UPDATE containers SET container=? WHERE name=?", (identity, record[0]))
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute("SELECT name FROM containers WHERE name=?", (record[0],)).fetchone()
                is None
            ):
                return True
            self.engine.remove(identity)
            require(self.engine.inspect(identity) is None, "CLEANUP_UNKNOWN", "container remains")
        elif not record[2]:
            return False
        db.execute("DELETE FROM containers WHERE name=?", (record[0],))
        return True

    def cleanup(self, name: str) -> bool:
        with self._db() as db:
            row = db.execute("SELECT * FROM containers WHERE name=?", (name,)).fetchone()
            return row is None or self._remove(db, row)

    def sweep(self, *, advertise: bool = False) -> None:
        with self._db() as db:
            identity = db.execute("SELECT engine FROM owner WHERE id=1").fetchone()[0]
            require(self.engine.identity() == identity, "BINDING", "Docker daemon changed")
            now = self.clock()
            managed = set(self.engine.managed())
            records = db.execute("SELECT * FROM containers ORDER BY name").fetchall()
            known = {identity for row in records if (identity := self._owned(row)) is not None}
            require(managed <= known, "BINDING", "unjournaled managed container")
            for row in records:
                if row[3] != self.boot or now >= row[4]:
                    self._remove(db, row)
            if advertise:
                db.execute("UPDATE owner SET boot=?, heartbeat=? WHERE id=1", (self.boot, now))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover only journal-owned expired gVisor containers"
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    recovery = Recovery(args.directory, Docker(args.docker), boot_id())
    while True:
        recovery.sweep(advertise=not args.once)
        if args.once:
            return
        time.sleep(0.25)


if __name__ == "__main__":
    main()
