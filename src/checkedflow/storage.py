"""Node-local atomic commits. No shared database chooses distributed ordering."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from checkedflow.core.model import State
from checkedflow.core.values import JSON, Object, obj, require
from checkedflow.serialization import decode, encode
from checkedflow.wire import digest, dumps, loads


class Store:
    def __init__(self, path: Path, initial: State) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.initial = deepcopy(initial)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1), "
                "height INTEGER NOT NULL, hash TEXT NOT NULL, body BLOB NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS blocks (height INTEGER PRIMARY KEY, body BLOB NOT NULL)"
            )
            row = db.execute("SELECT body FROM snapshot WHERE id=1").fetchone()
            if row is None:
                body = dumps(encode(initial))
                db.execute(
                    "INSERT INTO snapshot VALUES (1, ?, ?, ?)",
                    (initial.height, digest(encode(initial)), body),
                )
            else:
                old = decode(obj(loads(row[0])))
                require(
                    old.chain == initial.chain and old.organizations == initial.organizations,
                    "GENESIS",
                    "store belongs to another genesis",
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def load(self) -> State:
        with self._connect() as db:
            row = db.execute("SELECT body, hash FROM snapshot WHERE id=1").fetchone()
            require(row is not None, "STORAGE", "missing snapshot")
            value = obj(loads(row[0]))
            require(digest(value) == row[1], "STORAGE", "snapshot digest mismatch")
            return decode(value)

    def commit(self, state: State, transactions: list[JSON]) -> None:
        value, block = encode(state), dumps(transactions, string_limit=2097152)
        fingerprint, body = digest(value), dumps(value)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT height, hash FROM snapshot WHERE id=1").fetchone()
            require(current is not None, "STORAGE", "missing snapshot")
            if current[0] == state.height:
                prior = db.execute(
                    "SELECT body FROM blocks WHERE height=?", (state.height,)
                ).fetchone()
                require(
                    current[1] == fingerprint and prior is not None and prior[0] == block,
                    "STORAGE",
                    "conflicting repeated commit",
                )
                return
            require(
                state.height == current[0] + 1, "HEIGHT", "commit must advance exactly one block"
            )
            db.execute("INSERT INTO blocks VALUES (?, ?)", (state.height, block))
            db.execute(
                "UPDATE snapshot SET height=?, hash=?, body=? WHERE id=1",
                (state.height, fingerprint, body),
            )

    def blocks(self) -> list[Object]:
        return list(self.iter_blocks())

    def iter_blocks(self) -> Iterator[Object]:
        """Stream the local journal without retaining the complete archive in memory."""
        with self._connect() as db:
            for row in db.execute("SELECT height, body FROM blocks ORDER BY height"):
                yield {"height": row[0], "transactions": loads(row[1], string_limit=2097152)}
