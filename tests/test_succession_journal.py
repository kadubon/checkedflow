"""Crash and concurrent-writer protection for a single protected approval journal."""

import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from test_succession import fixture

from checkedflow.core.values import Failure
from checkedflow.succession_journal import ApprovalJournal
from checkedflow.wire import dumps


def settings(plan):
    return dict(
        chain=plan["old"]["chain"],
        mission=plan["old"]["mission"],
        side="new",
        organization="a",
        identity="a",
    )


class CountingSigner:
    def __init__(self):
        self.calls = 0

    def sign(self, message):
        self.calls += 1
        return bytes(64)


def test_reopen_preserves_plan_and_rejects_competitor(tmp_path):
    _, envelope, keys = fixture()
    plan = envelope["plan"]
    path = tmp_path / "approval.db"
    journal = ApprovalJournal(path, **settings(plan), create=True)
    key = next(iter(keys.values()))
    first = journal.sign(plan, key)
    journal.close()
    journal = ApprovalJournal(path, **settings(plan))
    assert journal.sign(plan, key) == first
    changed = deepcopy(plan)
    changed["new"]["chain"] += "-competitor"
    signer = CountingSigner()
    with pytest.raises(Failure, match="CONFLICT"):
        journal.sign(changed, signer)
    assert signer.calls == 0
    journal.close()


def test_claim_survives_actual_process_exit_inside_signer(tmp_path):
    _, envelope, _ = fixture()
    plan = envelope["plan"]
    path = tmp_path / "approval.db"
    ApprovalJournal(path, **settings(plan), create=True).close()
    script = """
import os, sys
from pathlib import Path
from checkedflow.succession_journal import ApprovalJournal
from checkedflow.wire import document
plan = document(sys.stdin.buffer.read())
class Crash:
    def sign(self, message):
        os._exit(73)
journal = ApprovalJournal(Path(sys.argv[1]), chain=plan['old']['chain'],
    mission=plan['old']['mission'], side='new', organization='a', identity='a')
journal.sign(plan, Crash())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        input=dumps(plan),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 73, result.stderr
    journal = ApprovalJournal(path, **settings(plan))
    changed = deepcopy(plan)
    changed["old"]["height"] += 1
    with pytest.raises(Failure, match="CONFLICT"):
        journal.sign(changed, CountingSigner())
    assert journal.sign(plan, CountingSigner())["identity"] == "a"
    journal.close()


def test_competing_connections_allow_only_one_plan(tmp_path):
    _, envelope, _ = fixture()
    plan = envelope["plan"]
    path = tmp_path / "approval.db"
    ApprovalJournal(path, **settings(plan), create=True).close()
    changed = deepcopy(plan)
    changed["new"]["chain"] += "-competitor"

    def run(candidate):
        journal = ApprovalJournal(path, **settings(plan))
        signer = CountingSigner()
        try:
            journal.sign(candidate, signer)
        except Failure as error:
            assert error.code == "CONFLICT"
        finally:
            journal.close()
        return signer.calls

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, (plan, changed))) == [0, 1]


def test_missing_foreign_partial_and_rebound_journals_fail_closed(tmp_path):
    _, envelope, _ = fixture()
    plan = envelope["plan"]
    path = tmp_path / "approval.db"
    with pytest.raises(sqlite3.OperationalError):
        ApprovalJournal(path, **settings(plan))
    assert not path.exists()
    journal = ApprovalJournal(path, **settings(plan), create=True)
    changed = deepcopy(plan)
    changed["old"]["mission"] += "-other"
    with pytest.raises(Failure, match="BINDING"):
        journal.sign(changed, CountingSigner())
    journal.db.execute("DELETE FROM approval")
    journal.db.commit()
    with pytest.raises(Failure, match="BINDING"):
        journal.sign(plan, CountingSigner())
    journal.close()
    with pytest.raises(Failure, match="BINDING"):
        ApprovalJournal(path, **settings(plan))
    with pytest.raises(FileExistsError):
        ApprovalJournal(path, **settings(plan), create=True)
    partial = tmp_path / "partial.db"
    partial.touch()
    with pytest.raises(Failure, match="VERSION"):
        ApprovalJournal(partial, **settings(plan))


def test_identity_binding_and_side(tmp_path):
    _, envelope, _ = fixture()
    plan = envelope["plan"]
    path = tmp_path / "approval.db"
    ApprovalJournal(path, **settings(plan), create=True).close()
    with pytest.raises(Failure, match="BINDING"):
        ApprovalJournal(path, **(settings(plan) | {"identity": "b"}))
    with pytest.raises(Failure, match="VERSION"):
        ApprovalJournal(path, **(settings(plan) | {"side": "worker"}))
