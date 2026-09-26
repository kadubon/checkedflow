"""Bounded local recovery buffers; unresolved execution is never capacity to reclaim."""

import sqlite3
from dataclasses import replace
from hashlib import sha256

import pytest
from test_worker_supervisor import Worker

from checkedflow.core.values import Failure
from checkedflow.worker_supervisor import MAX_LOCAL_ATTEMPTS, Outcome


def count(worker):
    with sqlite3.connect(worker.path / "execution" / "worker.sqlite") as db:
        return db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]


def finished(path):
    worker = Worker(path)
    assert worker.supervisor.step(worker.node.task) == "finished"
    worker.node.h.send("mission.pause", {})
    return worker


def another_task(worker):
    worker.node.h.send("mission.resume", {})
    ticket, _ = worker.node.h.send(
        "budget.reserve", {"phase": "execute", "ceiling": 30, "target": "a" * 64}
    )
    task, _ = worker.node.h.send(
        "task.admit",
        {
            "ticket": ticket,
            "workers": ["worker"],
            "lease_blocks": 100,
            "expires": 100,
            "max_attempts": 2,
        },
    )
    return task


def test_local_capacity_denies_before_lease_start_or_execution(tmp_path):
    w = Worker(tmp_path)
    with sqlite3.connect(tmp_path / "execution" / "worker.sqlite") as db:
        db.executemany(
            "INSERT INTO attempts VALUES (?, 1, 1, NULL, NULL)",
            [(f"unresolved-{i}",) for i in range(MAX_LOCAL_ATTEMPTS)],
        )
    before = w.node.read()
    with pytest.raises(Failure, match="CAPACITY"):
        w.supervisor.step(w.node.task)
    assert w.node.read() == before and not w.node.sent and w.calls == 0
    assert count(w) == MAX_LOCAL_ATTEMPTS


def test_retained_finished_buffer_frees_capacity_without_reexecuting(tmp_path, monkeypatch):
    monkeypatch.setattr("checkedflow.worker_supervisor.MAX_LOCAL_ATTEMPTS", 1)
    w = finished(tmp_path)
    assert w.supervisor.retire((w.node.task,)) == 1
    assert count(w) == 0 and len(w.published) == 2 and w.calls == 1
    assert w.supervisor.retire((w.node.task,)) == 0
    assert w.reopen().step(w.node.task) == "finished" and w.calls == 1
    task = another_task(w)
    assert w.reopen().step(task) == "finished"
    assert w.calls == 2 and count(w) == 1 and w.node.read().budget.spent == 60


def test_retirement_requires_pause_and_known_finished_results(tmp_path):
    w = Worker(tmp_path)
    with pytest.raises(Failure, match="SHAPE"):
        w.supervisor.retire(())
    with pytest.raises(Failure, match="DUPLICATE"):
        w.supervisor.retire((w.node.task, w.node.task))
    with pytest.raises(Failure, match="PAUSED"):
        w.supervisor.retire((w.node.task,))
    w.reopen(execute=lambda *a: Outcome("unknown", b"{}")).step(w.node.task)
    w.node.h.send("mission.pause", {})
    with pytest.raises(Failure, match="PENDING"):
        w.supervisor.retire((w.node.task,))
    assert count(w) == 1
    with pytest.raises(Failure, match="NOT_FOUND"):
        w.supervisor.retire(("0:missing",))


def test_pending_command_or_concurrent_worker_blocks_retirement(tmp_path):
    w = Worker(tmp_path)
    w.node.behavior = "before"
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        w.supervisor.step(w.node.task)
    w.node.h.send("mission.pause", {})
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        w.supervisor.retire((w.node.task,))
    with sqlite3.connect(tmp_path / "execution" / "worker-lock.sqlite") as lock:
        lock.execute("BEGIN IMMEDIATE")
        with pytest.raises(Failure, match="BUSY"):
            w.supervisor.retire((w.node.task,))


@pytest.mark.parametrize(
    "column,value",
    [
        ("fence", 2),
        ("revision", 2),
        ("outcome", "unknown"),
        ("evidence", None),
        ("evidence", 1000000000000),
        ("evidence", b"wrong"),
    ],
)
def test_corrupt_local_completion_is_not_retired_or_published(tmp_path, column, value):
    w = finished(tmp_path)
    with sqlite3.connect(tmp_path / "execution" / "worker.sqlite") as db:
        db.execute(f"UPDATE attempts SET {column}=?", (value,))
    with pytest.raises(Failure, match="BINDING"):
        w.supervisor.retire((w.node.task,))
    assert count(w) == 1 and len(w.published) == 1


@pytest.mark.parametrize("change", ["unavailable", "digest", "resume", "completion"])
def test_retention_failure_or_state_change_preserves_local_buffer(tmp_path, change):
    w = finished(tmp_path)

    def retain(state, task, evidence):
        if change == "unavailable":
            raise OSError("fixture unavailable")
        if change == "digest":
            return "d" * 64
        if change == "resume":
            w.node.h.send("mission.resume", {})
        else:
            state = w.node.read()
            w.node.h.runtime._state = replace(
                state,
                height=state.height + 1,
                tasks=(replace(state.tasks[0], evidence="f" * 64),),
            )
        return sha256(evidence).hexdigest()

    with pytest.raises((Failure, OSError)):
        w.reopen(publish=retain).retire((w.node.task,))
    assert count(w) == 1 and w.calls == 1


def test_batch_retirement_is_atomic_even_if_earlier_evidence_was_retained(tmp_path):
    w = finished(tmp_path)
    other = another_task(w)
    assert w.supervisor.step(other) == "finished"
    w.node.h.send("mission.pause", {})

    def retain(state, task, evidence):
        return sha256(evidence).hexdigest() if task.identity == w.node.task else "d" * 64

    with pytest.raises(Failure, match="BINDING"):
        w.reopen(publish=retain).retire((w.node.task, other))
    assert count(w) == 2 and w.calls == 2
    assert w.reopen().retire((w.node.task, other)) == 2
    assert count(w) == 0 and w.node.read().budget.spent == 60


@pytest.mark.parametrize("phase", ["publication", "committed"])
def test_process_exit_during_buffer_retirement_preserves_recovery(tmp_path, phase):
    import subprocess
    import sys
    from pathlib import Path

    from test_worker_submission import Node

    from checkedflow.dispatch_watchdog import Watchdog
    from checkedflow.operational_codec import decode
    from checkedflow.operational_runtime import Runtime
    from checkedflow.worker_supervisor import Supervisor

    child = r"""
import os, sys
from pathlib import Path
from hashlib import sha256
sys.path.insert(0, sys.argv[1])
from test_worker_retirement import finished
from checkedflow.operational_codec import state_bytes
root = Path(sys.argv[2])
w = finished(root)
(root / 'state').write_bytes(state_bytes(w.node.read()))
def retain(state, task, evidence):
    (root / 'retained').write_bytes(evidence)
    if sys.argv[3] == 'publication':
        os._exit(39)
    return sha256(evidence).hexdigest()
w.reopen(publish=retain).retire((w.node.task,))
os._exit(39)
"""
    result = subprocess.run(
        [sys.executable, "-c", child, str(Path(__file__).parent), str(tmp_path), phase],
        timeout=30,
        check=False,
    )
    assert result.returncode == 39
    node = Node()
    node.h.runtime = Runtime(decode((tmp_path / "state").read_bytes()))
    coordinator = node.coordinator(tmp_path / "commands")
    watchdog = Watchdog(
        node.read,
        chain=node.read().chain,
        mission="m",
        max_read_age_ns=10000000000,
        max_stall_ns=10000000000,
    )

    def never_execute(*args):
        pytest.fail("retired work was executed again")

    def retained(state, task, evidence):
        assert (tmp_path / "retained").read_bytes() == evidence
        return sha256(evidence).hexdigest()

    resumed = Supervisor(tmp_path / "execution", coordinator, watchdog, never_execute, retained)
    assert resumed.retire((node.task,)) == (1 if phase == "publication" else 0)
    assert resumed.step(node.task) == "finished"
    assert node.read().budget.spent == 30
