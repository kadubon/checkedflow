"""Partial journal damage must never silently reset ownership, limits or unknown work."""

import sqlite3
from contextlib import closing

import pytest
from agent_backend import Backend
from test_sandbox_recovery import BOOT, Engine
from test_worker_schedule import setup
from test_worker_supervisor import Worker

from checkedflow.agents.journal import Journal
from checkedflow.core.values import Failure
from checkedflow.sandbox_recovery import Recovery


@pytest.fixture(params=["worker", "schedule", "sandbox", "agent"])
def journal(request, tmp_path):
    if request.param == "worker":
        worker = Worker(tmp_path)
        return tmp_path / "execution/worker.sqlite", worker.reopen, "identity", "attempts"
    if request.param == "schedule":
        _, _, _, reopen = setup(tmp_path)
        reopen()
        return tmp_path / "schedule/schedule.sqlite", reopen, "schedule", "queue"
    if request.param == "sandbox":
        engine = Engine()

        def reopen():
            return Recovery(tmp_path, engine, BOOT)

        reopen()
        return tmp_path / "sandbox.sqlite", reopen, "owner", "containers"
    gateway = Backend().gateway()
    path = tmp_path / "agents.sqlite"

    def reopen():
        Journal(gateway, path).close()

    reopen()
    return path, reopen, "settings", "observations"


@pytest.mark.parametrize("damage", ["identity", "table", "foreign"])
def test_partial_journal_never_reinitializes(journal, damage):
    path, reopen, identity, records = journal
    # Table names are fixed fixture literals, never input to an application SQL API.
    with closing(sqlite3.connect(path)) as db, db:
        if damage == "identity":
            db.execute(f"DELETE FROM {identity}")
        elif damage == "table":
            db.execute(f"DROP TABLE {records}")
        else:
            db.execute("CREATE TABLE foreign_profile (id INTEGER)")
    with closing(sqlite3.connect(path)) as db:
        before = tuple(db.iterdump())
    with pytest.raises(Failure, match="STORAGE|VERSION"):
        reopen()
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("key", ["binding", "cursor_key"])
def test_missing_agent_identity_component_is_not_regenerated(tmp_path, key):
    gateway = Backend().gateway()
    path = tmp_path / "agent.sqlite"
    Journal(gateway, path).close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM settings WHERE key=?", (key,))
    with pytest.raises(Failure, match="STORAGE"):
        Journal(gateway, path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone() is None


@pytest.mark.parametrize("kind", ["worker", "schedule", "agent"])
def test_first_initialization_interruption_leaves_no_partial_schema(tmp_path, monkeypatch, kind):
    def crash(*args):
        raise SystemExit("fixture initialization interruption")

    if kind == "worker":
        import checkedflow.worker_supervisor as module

        worker = Worker(tmp_path)
        path = tmp_path / "fresh/worker.sqlite"

        def reopen():
            return module.Supervisor(
                path.parent, worker.coordinator, worker.watchdog, worker.execute, worker.publish
            )

        target, name = module, "dumps"
    elif kind == "schedule":
        from checkedflow.worker_schedule import Schedule

        _, _, _, reopen = setup(tmp_path)
        path = tmp_path / "schedule/schedule.sqlite"
        target, name = Schedule, "_now"
    else:
        import checkedflow.agents.journal as module

        gateway = Backend().gateway()
        path = tmp_path / "agent.sqlite"

        def reopen():
            Journal(gateway, path).close()

        target, name = module.secrets, "token_hex"
    with monkeypatch.context() as patch:
        patch.setattr(target, name, crash)
        with pytest.raises(SystemExit):
            reopen()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    reopen()
