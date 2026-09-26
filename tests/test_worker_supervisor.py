"""Benign observer fixtures test supervision; candidate execution remains in gVisor tests."""

import sqlite3
from dataclasses import replace
from hashlib import sha256

import pytest
from test_worker_submission import Node

from checkedflow.core.values import Failure
from checkedflow.dispatch_watchdog import Watchdog
from checkedflow.worker_supervisor import Outcome, Supervisor


class Worker:
    def __init__(self, path):
        self.path = path
        self.node = Node()
        self.calls = 0
        self.published = []
        self.coordinator = self.node.coordinator(path / "commands")
        self.watchdog = Watchdog(
            self.progress,
            chain=self.node.h.initial.chain,
            mission="m",
            max_read_age_ns=10_000_000_000,
            max_stall_ns=10_000_000_000,
        )
        self.watchdog.poll()
        self.supervisor = self.reopen()

    def progress(self):
        self.node.h.runtime.tick(self.node.read().height + 1)
        return self.node.read()

    def execute(self, state, task):
        self.calls += 1
        return Outcome("reported", b'{"fixture":"benign-observer"}')

    def publish(self, state, task, raw):
        self.published.append(raw)
        return sha256(raw).hexdigest()

    def reopen(self, execute=None, publish=None):
        return Supervisor(
            self.path / "execution",
            self.coordinator,
            self.watchdog,
            execute or self.execute,
            publish or self.publish,
        )


def test_run_once_and_resume_without_reexecuting(tmp_path):
    w = Worker(tmp_path)
    assert w.supervisor.step(w.node.task) == "finished"
    assert w.calls == 1 and len(w.published) == 1
    assert w.reopen().step(w.node.task) == "finished"
    assert w.calls == 1
    assert w.node.read().budget.spent == 30


def test_publication_failure_retains_report_without_rerunning(tmp_path):
    w = Worker(tmp_path)

    def unavailable(*args):
        raise OSError("fixture storage unavailable")

    with pytest.raises(OSError):
        w.reopen(publish=unavailable).step(w.node.task)
    assert w.calls == 1
    assert w.reopen().step(w.node.task) == "finished"
    assert w.calls == 1


def test_interrupted_observer_never_runs_again(tmp_path):
    w = Worker(tmp_path)

    def interrupted(*args):
        w.calls += 1
        raise SystemExit("fixture abrupt interruption")

    with pytest.raises(SystemExit):
        w.reopen(execute=interrupted).step(w.node.task)
    assert w.reopen().step(w.node.task) == "unknown"
    assert w.calls == 1 and b"worker_interrupted" in w.published[0]
    assert w.node.read().budget.spent == 30


def test_already_started_remote_attempt_without_local_record_is_unknown(tmp_path):
    w = Worker(tmp_path)
    task = w.node.task
    w.node.h.send("task.lease", {"task": task}, "worker")
    w.node.h.send("task.start", {"task": task, "fence": 1}, "worker")
    assert w.supervisor.step(task) == "unknown"
    assert w.calls == 0


@pytest.mark.parametrize(
    "result",
    [
        Outcome("bad", b"{}"),
        Outcome("reported", b""),
        Outcome("reported", b"invalid"),
        Outcome("reported", b"x" * 1048577),
        RuntimeError("secret fixture detail"),
    ],
)
def test_failed_or_invalid_observation_is_explicit_unknown(tmp_path, result):
    w = Worker(tmp_path)

    def execute(*args):
        if isinstance(result, Exception):
            raise result
        return result

    assert w.reopen(execute=execute).step(w.node.task) == "unknown"
    assert b"worker_observation_failed" in w.published[0]
    assert b"secret fixture detail" not in w.published[0]


def test_pause_and_local_emergency_stop_inhibit_new_execution(tmp_path):
    w = Worker(tmp_path)
    w.node.h.send("mission.pause", {})
    with pytest.raises(Failure, match="NOT_READY"):
        w.supervisor.step(w.node.task)
    assert w.calls == 0
    w.watchdog.stop()
    with pytest.raises(Failure, match="STOPPED"):
        w.supervisor.step(w.node.task)
    assert w.calls == 0


def test_pause_after_execution_allows_historical_completion(tmp_path):
    w = Worker(tmp_path)

    def execute(state, task):
        w.node.h.send("mission.pause", {})
        return w.execute(state, task)

    assert w.reopen(execute=execute).step(w.node.task) == "finished"
    assert w.node.read().mode == "paused" and w.calls == 1


def test_heartbeat_shares_nonce_sequence_with_completion(tmp_path):
    w = Worker(tmp_path)

    def execute(state, task):
        w.supervisor.heartbeat(task.identity)
        return w.execute(state, task)

    assert w.reopen(execute=execute).step(w.node.task) == "finished"
    assert dict(w.node.read().journal.actors)["worker"] == 4


def test_lost_finish_reply_reconciles_without_repeat_execution(tmp_path):
    w = Worker(tmp_path)

    def execute(state, task):
        w.node.behavior = "after"
        return w.execute(state, task)

    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        w.reopen(execute=execute).step(w.node.task)
    assert w.reopen().step(w.node.task) == "finished"
    assert w.calls == 1


def test_corrupt_publisher_and_changed_completion_fence_are_rejected(tmp_path):
    w = Worker(tmp_path)
    with pytest.raises(Failure, match="published evidence differs"):
        w.reopen(publish=lambda *args: "f" * 64).step(w.node.task)
    assert w.calls == 1

    def changed(state, task, raw):
        current = w.node.read()
        w.node.h.runtime._state = replace(
            current, height=current.height + 1, tasks=(replace(task, fence=task.fence + 1),)
        )
        return w.publish(state, task, raw)

    with pytest.raises(Failure, match="completion ownership"):
        w.reopen(publish=changed).step(w.node.task)
    assert w.calls == 1


def test_terminal_observation_during_publication_does_not_forge_finish(tmp_path):
    w = Worker(tmp_path)

    def cancel(state, task, raw):
        w.node.h.send("task.cancel", {"task": task.identity})
        return w.publish(state, task, raw)

    assert w.reopen(publish=cancel).step(w.node.task) == "unknown"
    assert w.calls == 1


def test_scope_busy_missing_and_ownership_denials(tmp_path):
    w = Worker(tmp_path)
    with pytest.raises(Failure, match="NOT_FOUND"):
        w.supervisor.step("0:missing")
    with sqlite3.connect(tmp_path / "execution/worker-lock.sqlite") as lock:
        lock.execute("BEGIN IMMEDIATE")
        with pytest.raises(Failure, match="BUSY"):
            w.supervisor.step(w.node.task)
    w.node.h.send("task.lease", {"task": w.node.task}, "other")
    with pytest.raises(Failure):
        w.supervisor.step(w.node.task)
    assert w.calls == 0
    foreign = Watchdog(w.node.read, chain="foreign", mission="m", max_read_age_ns=1, max_stall_ns=1)
    with pytest.raises(Failure, match="scope"):
        Supervisor(tmp_path, w.coordinator, foreign, w.execute, w.publish)


@pytest.mark.parametrize("window", ["execution", "publication"])
def test_real_process_exit_preserves_attempt_and_result(tmp_path, window):
    import subprocess
    import sys
    from pathlib import Path

    from checkedflow.operational_codec import decode
    from checkedflow.operational_runtime import Runtime

    child = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from test_worker_supervisor import Worker
from checkedflow.operational_codec import state_bytes
root = Path(sys.argv[2])
worker = Worker(root)
def execute(state, task):
    (root / "node.json").write_bytes(state_bytes(state))
    (root / "called").write_text("once")
    if sys.argv[3] == "execution":
        os._exit(39)
    return worker.execute(state, task)
def publish(state, task, raw):
    os._exit(39)
worker.reopen(execute=execute, publish=publish).step(worker.node.task)
"""
    result = subprocess.run(
        [sys.executable, "-c", child, str(Path(__file__).parent), str(tmp_path), window],
        timeout=30,
        check=False,
    )
    assert result.returncode == 39
    node = Node()
    node.h.runtime = Runtime(decode((tmp_path / "node.json").read_bytes()))
    coordinator = node.coordinator(tmp_path / "commands")
    watchdog = Watchdog(
        node.read,
        chain=node.h.initial.chain,
        mission="m",
        max_read_age_ns=10_000_000_000,
        max_stall_ns=10_000_000_000,
    )
    calls = []

    def forbidden(*args):
        calls.append(True)
        raise AssertionError("interrupted candidate must not be executed again")

    supervisor = Supervisor(
        tmp_path / "execution",
        coordinator,
        watchdog,
        forbidden,
        lambda s, t, raw: sha256(raw).hexdigest(),
    )
    assert supervisor.step(node.task) == ("unknown" if window == "execution" else "finished")
    assert not calls and (tmp_path / "called").read_text() == "once"


def test_execution_journal_cannot_cross_deployment_origins(tmp_path):
    w = Worker(tmp_path)
    state = w.node.read()
    w.node.h.runtime._state = replace(
        state,
        height=state.height + 1,
        credentials=(replace(state.credentials[0], public_key="f" * 64), *state.credentials[1:]),
    )
    other = w.node.coordinator(tmp_path / "another-coordinator")
    with pytest.raises(Failure, match="another identity"):
        Supervisor(tmp_path / "execution", other, w.watchdog, w.execute, w.publish)
