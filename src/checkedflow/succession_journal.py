"""Durably pin one reviewed succession before invoking a protected signer.

The journal must move with signer custody. It cannot detect a restored old copy,
an operator bypassing this API, or another independently provisioned signer.
"""

import sqlite3
from pathlib import Path

from checkedflow.core.values import Object, obj, require, text
from checkedflow.operational_identity import Signer
from checkedflow.succession import approval_message, approve
from checkedflow.wire import document, dumps


class ApprovalJournal:
    """One old mission and administrator; explicitly create once, then reopen.

    Claims survive signer exceptions and process death. Re-signing the same plan
    is safe; approving any competing plan requires separate governed recovery,
    which this adapter deliberately does not implement.
    """

    def __init__(
        self,
        path: Path,
        *,
        chain: str,
        mission: str,
        side: str,
        organization: str,
        identity: str,
        create: bool = False,
    ) -> None:
        require(side in {"old", "new"}, "VERSION", "approval side")
        self.chain, self.mission = text(chain, limit=128), text(mission)
        self.side = side
        self.organization, self.identity = text(organization, limit=80), text(identity, limit=80)
        binding = dumps(
            {
                "profile": "checkedflow/succession-journal/v1",
                "chain": chain,
                "mission": mission,
                "side": side,
                "organization": organization,
                "identity": identity,
            }
        )
        if create:
            # Exclusive creation never initializes over a damaged or existing journal.
            with path.open("xb"):
                pass
            path.chmod(0o600)
        self.db = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                if create:
                    self.db.execute(
                        "CREATE TABLE approval (id INTEGER PRIMARY KEY CHECK(id=1), "
                        "binding BLOB NOT NULL, plan BLOB)"
                    )
                    self.db.execute("INSERT INTO approval VALUES (1, ?, NULL)", (binding,))
                tables = self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
                require(tables == [("approval",)], "VERSION", "approval journal schema")
                rows = self.db.execute("SELECT id,binding,plan FROM approval").fetchall()
                require(
                    len(rows) == 1 and rows[0][0] == 1 and rows[0][1] == binding,
                    "BINDING",
                    "approval journal identity missing or different",
                )
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def sign(self, plan: Object, signer: Signer) -> Object:
        """Persist the explicitly reviewed plan before any signer invocation."""
        approval_message(plan, self.side, self.organization, self.identity)
        old = obj(plan["old"])
        require(
            old.get("chain") == self.chain and old.get("mission") == self.mission,
            "BINDING",
            "approval journal old mission differs",
        )
        encoded = dumps(plan)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            rows = self.db.execute("SELECT plan FROM approval WHERE id=1").fetchall()
            require(len(rows) == 1, "BINDING", "approval claim missing; recovery required")
            require(rows[0][0] in (None, encoded), "CONFLICT", "competing succession plan")
            self.db.execute("UPDATE approval SET plan=? WHERE id=1", (encoded,))
        # Never roll back a durable claim because a signer fails or its reply is lost.
        return approve(document(encoded), self.side, self.organization, self.identity, signer)
