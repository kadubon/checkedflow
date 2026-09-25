"""Durable transport observations. This index is never execution or consensus authority."""

import base64
import hmac
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from checkedflow.agents.gateway import Gateway
from checkedflow.core.values import Object, array, obj, require
from checkedflow.serialization import encode
from checkedflow.wire import digest, document, dumps


class Journal:
    """One mission, one gateway process. Persist monotonic observation times and push state."""

    def __init__(
        self, gateway: Gateway, path: Path | None = None, *, clock: Callable[[], int] = time.time_ns
    ) -> None:
        self.gateway, self.clock = gateway, clock
        self.lock = threading.RLock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        if path is not None:
            path.chmod(0o600)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, stamp INTEGER NOT NULL,
                value BLOB NOT NULL, history BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS notifications (
                task TEXT NOT NULL, id TEXT NOT NULL, value BLOB NOT NULL,
                delivered TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(task,id));
        """)
        binding = digest({"chain": gateway.chain, "mission": gateway.mission})
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO settings VALUES ('binding', ?)", (binding,))
            require(
                self.setting("binding") == binding, "SCOPE", "journal belongs to another mission"
            )
            self.db.execute(
                "INSERT OR IGNORE INTO settings VALUES ('cursor_key', ?)", (secrets.token_hex(32),)
            )

    def setting(self, key: str) -> str:
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else ""

    def close(self) -> None:
        self.db.close()

    def refresh(self) -> list[str]:
        # Serialize refreshes as well as writes so an older in-flight response cannot win.
        with self.lock:
            state = self.gateway.state()
            encoded = encode(state)
            state_hash = digest(encoded)
            changed: list[str] = []
            with self.db:
                last = int(
                    self.db.execute("SELECT COALESCE(MAX(stamp),0) FROM observations").fetchone()[0]
                )
                for identity, task in state.tasks.items():
                    if task.mission != self.gateway.mission:
                        continue
                    record = obj(encoded["tasks"])[identity]
                    capabilities: Object = {
                        key: {"status": cap.status, "source_digest": cap.source_digest}
                        for key, cap in state.capabilities.items()
                        if cap.task == identity and cap.mission == self.gateway.mission
                    }
                    fingerprint = digest({"record": record, "capabilities": capabilities})
                    row = self.db.execute(
                        "SELECT fingerprint, history FROM observations WHERE id=?", (identity,)
                    ).fetchone()
                    if row and row[0] == fingerprint:
                        continue
                    last = max(self.clock(), last + 1)
                    history = array(document(row[1])["items"]) if row else []
                    history.append({"status": task.status, "height": state.height})
                    # History is a documented bounded observation history, not a block journal.
                    value: Object = {
                        "chain": state.chain,
                        "mission": self.gateway.mission,
                        "height": state.height,
                        "state_hash": state_hash,
                        "record": record,
                        "capabilities": capabilities,
                    }
                    self.db.execute(
                        "INSERT OR REPLACE INTO observations VALUES (?, ?, ?, ?, ?)",
                        (
                            identity,
                            fingerprint,
                            last,
                            dumps(value),
                            dumps({"items": history[-64:]}),
                        ),
                    )
                    changed.append(identity)
            return changed

    def get(self, identity: str) -> tuple[Object, int, list[Object], str]:
        with self.lock:
            row = self.db.execute(
                "SELECT value,stamp,history,fingerprint FROM observations WHERE id=?", (identity,)
            ).fetchone()
        require(row is not None, "NOT_FOUND", "task not found in this mission")
        entries = array(document(row[2])["items"])
        return document(row[0]), int(row[1]), [obj(v) for v in entries], str(row[3])

    def ordered(self) -> list[str]:
        with self.lock:
            return [
                str(row[0])
                for row in self.db.execute(
                    "SELECT id FROM observations ORDER BY stamp DESC,id DESC"
                )
            ]

    def cursor(self, value: Object) -> str:
        raw = dumps(value)
        signature = hmac.digest(bytes.fromhex(self.setting("cursor_key")), raw, "sha256")
        return base64.urlsafe_b64encode(signature + raw).decode()

    def read_cursor(self, token: str) -> Object:
        require(len(token) <= 2048, "CURSOR", "cursor limit")
        try:
            raw = base64.b64decode(token, altchars=b"-_", validate=True)
        except ValueError as exc:
            raise ValueError("invalid cursor encoding") from exc
        expected = hmac.digest(bytes.fromhex(self.setting("cursor_key")), raw[32:], "sha256")
        require(hmac.compare_digest(raw[:32], expected), "CURSOR", "cursor authentication failed")
        return document(raw[32:])

    def configurations(self, task: str) -> list[Object]:
        with self.lock:
            return [
                document(row[0])
                for row in self.db.execute(
                    "SELECT value FROM notifications WHERE task=? ORDER BY id", (task,)
                )
            ]

    def put_config(self, task: str, identity: str, value: Object) -> None:
        with self.lock, self.db:
            count = self.db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
            exists = self.db.execute(
                "SELECT 1 FROM notifications WHERE task=? AND id=?", (task, identity)
            ).fetchone()
            require(count < 256 or exists, "LIMIT", "push configuration limit")
            self.db.execute(
                "INSERT OR REPLACE INTO notifications(task,id,value) VALUES(?,?,?)",
                (task, identity, dumps(value)),
            )

    def delete_config(self, task: str, identity: str) -> None:
        with self.lock, self.db:
            deleted = self.db.execute(
                "DELETE FROM notifications WHERE task=? AND id=?", (task, identity)
            ).rowcount
            require(deleted == 1, "NOT_FOUND", "push configuration not found")

    def pending(self) -> list[tuple[str, str, Object, str, int]]:
        with self.lock:
            return [
                (str(t), str(i), document(v), str(f), int(a))
                for t, i, v, f, a in self.db.execute(
                    "SELECT n.task,n.id,n.value,o.fingerprint,n.attempts FROM notifications n "
                    "JOIN observations o ON o.id=n.task WHERE n.delivered != o.fingerprint"
                )
            ]

    def delivery(
        self, task: str, identity: str, configuration: Object, fingerprint: str, success: bool
    ) -> None:
        with self.lock, self.db:
            if success:
                self.db.execute(
                    "UPDATE notifications SET delivered=?,attempts=0 "
                    "WHERE task=? AND id=? AND value=?",
                    (fingerprint, task, identity, dumps(configuration)),
                )
            else:
                self.db.execute(
                    "UPDATE notifications SET attempts=attempts+1 "
                    "WHERE task=? AND id=? AND value=?",
                    (task, identity, dumps(configuration)),
                )
