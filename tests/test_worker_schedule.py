"""Persistent scheduling uses the real worker journals and benign trusted observers."""

import sqlite3
from dataclasses import replace

import pytest
from test_worker_supervisor import Worker

from checkedflow.core.values import Failure
from checkedflow.worker_schedule import Policy, Schedule


class Clock:
    now = 1000000000

    def __call__(self):
        return self.now


def setup(path, **options):
    worker = Worker(path)
    clock = Clock()
    policy = Policy((worker.node.task,), **options)

    def reopen(supervisor=None, **kw):
        return Schedule(
            path / "schedule",
            supervisor or worker.supervisor,
            policy,
            clock_epoch="test-boot",
            clock=clock,
            jitter=lambda n: n - 1,
            **kw,
        )

    return worker, clock, policy, reopen


def test_persisted_completion_never_reexecutes(tmp_path):
    w, clock, policy, reopen = setup(tmp_path)
    s = reopen()
    assert s.status().mode == "waiting"
    assert s.tick().mode == "complete"
    assert reopen().run().mode == "complete"
    assert w.calls == 1 and w.node.read().budget.spent == 30
    assert s.status().attempts == 1


def test_evidence_outage_retries_same_execution_with_bounded_backoff(tmp_path):
    w, clock, policy, reopen = setup(tmp_path, attempts_per_task=3)

    def unavailable(*a):
        raise OSError("secret not logged")

    s = reopen(w.reopen(publish=unavailable))
    assert s.tick().wait_ns == 250000000
    assert s.tick().attempts == 1 and w.calls == 1
    clock.now += 250000000
    assert s.tick().wait_ns == 500000000
    clock.now += 500000000
    assert reopen().tick().mode == "complete"
    assert w.calls == 1 and w.node.read().budget.spent == 30


@pytest.mark.parametrize("error", [Failure("PAUSED", "private"), OSError("private")])
def test_attempt_ceiling_survives_restart(tmp_path, error):
    w, clock, policy, reopen = setup(tmp_path, attempts_per_task=2)

    def fail(*a):
        raise error

    w.supervisor.step = fail
    s = reopen()
    s.tick()
    clock.now += 250000000
    assert reopen().tick().mode == "blocked"
    assert reopen().tick().attempts == 2
    assert w.calls == 0


def test_total_budget_and_deadline_do_not_reset(tmp_path):
    w, clock, policy, reopen = setup(tmp_path, total_attempts=1)
    w.supervisor.step = lambda t: (_ for _ in ()).throw(Failure("NOT_READY", "fixture"))
    assert reopen().tick().mode == "exhausted"
    assert reopen().tick().attempts == 1
    w2, c2, p2, r2 = setup(tmp_path / "deadline", duration_seconds=1)
    r2()
    c2.now += 1000000000
    assert r2().tick().mode == "exhausted" and w2.calls == 0


@pytest.mark.parametrize("change", ["policy", "epoch", "worker"])
def test_plan_binding_cannot_reset_limits(tmp_path, change):
    w, clock, policy, reopen = setup(tmp_path)
    reopen()
    if change == "worker":
        w = Worker(tmp_path / "other")
    with pytest.raises(Failure, match="SCOPE"):
        Schedule(
            tmp_path / "schedule",
            w.supervisor,
            replace(policy, total_attempts=200) if change == "policy" else policy,
            clock_epoch="other" if change == "epoch" else "test-boot",
            clock=clock,
        )


@pytest.mark.parametrize("error", [Failure("FENCE", "secret"), RuntimeError("secret"), None])
def test_unknown_failure_is_not_automatically_retried(tmp_path, error):
    w, clock, policy, reopen = setup(tmp_path)

    def fail(*a):
        if error is not None:
            raise error
        return "unrecognized"

    w.supervisor.step = fail
    assert reopen().tick().mode == "blocked"
    assert reopen().tick().attempts == 1
    with sqlite3.connect(tmp_path / "schedule" / "schedule.sqlite") as db:
        assert db.execute("SELECT reason FROM queue").fetchone()[0] in {"ADAPTER", "REJECTED"}


def test_interrupted_execution_consumes_attempt_and_remains_unknown(tmp_path):
    w, clock, policy, reopen = setup(tmp_path)

    def crash(*a):
        w.calls += 1
        raise SystemExit(39)

    with pytest.raises(SystemExit):
        reopen(w.reopen(execute=crash)).tick()
    assert reopen().tick().attempts == 1
    clock.now += 250000000
    assert reopen().tick().mode == "blocked"
    assert w.calls == 1 and w.node.read().tasks[0].status == "unknown"


def test_inflight_last_attempt_is_not_granted_again(tmp_path):
    w, clock, policy, reopen = setup(tmp_path, attempts_per_task=1)
    w.supervisor.step = lambda t: (_ for _ in ()).throw(SystemExit(39))
    with pytest.raises(SystemExit):
        reopen().tick()
    clock.now += 250000000
    assert reopen().tick().mode == "blocked"
    assert reopen().status().attempts == 1


def test_stop_persists_and_concurrent_schedule_is_rejected(tmp_path):
    w, clock, policy, reopen = setup(tmp_path)
    s = reopen()
    with sqlite3.connect(tmp_path / "schedule" / "schedule-lock.sqlite") as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(Failure, match="BUSY"):
            s.tick()
        s.stop()
    assert reopen().run().mode == "stopped" and w.calls == 0


@pytest.mark.parametrize("value", [0, -1, True, 2**63])
def test_invalid_or_backward_clock_latches_stop(tmp_path, value):
    w, clock, policy, reopen = setup(tmp_path)
    s = reopen()
    clock.now = value
    with pytest.raises(Failure, match="CLOCK"):
        s.tick()
    clock.now = 2000000000
    assert reopen().tick().mode == "stopped"


def test_run_has_finite_poll_bound_and_interruptible_wait(tmp_path):
    w, clock, policy, reopen = setup(tmp_path)
    w.supervisor.step = lambda t: (_ for _ in ()).throw(Failure("STALE", "fixture"))
    s = reopen()
    assert s.run(max_polls=2).attempts == 1
    with pytest.raises(Failure):
        s.run(max_polls=0)


def test_deadline_and_stop_rechecked_before_invocation(tmp_path):
    w, clock, policy, reopen = setup(tmp_path, duration_seconds=1)
    s = reopen()
    original = s.status

    def expire_after_observation():
        result = original()
        clock.now += 1000000000
        return result

    s.status = expire_after_observation
    assert s.tick().mode == "exhausted" and w.calls == 0
    w2, c2, p2, r2 = setup(tmp_path / "stop")
    s2 = r2()

    def inhibit_during_admission(bound):
        s2._stop.set()
        return 0

    s2._jitter = inhibit_during_admission
    assert s2.tick().mode == "stopped" and w2.calls == 0


def test_invalid_jitter_cannot_dispatch(tmp_path):
    w, clock, policy, reopen = setup(tmp_path)
    s = reopen()
    s._jitter = lambda bound: bound
    with pytest.raises(Failure):
        s.tick()
    assert s.status().attempts == 0 and w.calls == 0


def test_failed_task_does_not_starve_another_admitted_task(tmp_path):
    from test_worker_retirement import another_task

    w, clock, policy, reopen = setup(tmp_path)
    other = another_task(w)
    step = w.supervisor.step

    def select(task):
        if task == w.node.task:
            raise Failure("PAUSED", "fixture retry")
        return step(task)

    w.supervisor.step = select
    s = Schedule(
        tmp_path / "schedule",
        w.supervisor,
        replace(policy, tasks=(w.node.task, other)),
        clock_epoch="test-boot",
        clock=clock,
        jitter=lambda bound: 0,
    )
    assert s.tick().remaining == 2
    assert s.tick().remaining == 1 and w.calls == 1


def test_actual_process_exit_does_not_reset_scheduler_or_execution(tmp_path):
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
sys.path.insert(0, sys.argv[1])
from test_worker_schedule import setup
from checkedflow.operational_codec import state_bytes
root = Path(sys.argv[2])
w, clock, policy, reopen = setup(root)
def crash(state, task):
    (root / 'state').write_bytes(state_bytes(w.node.read()))
    (root / 'executed').write_text('once')
    os._exit(39)
reopen(w.reopen(execute=crash)).tick()
"""
    result = subprocess.run(
        [sys.executable, "-c", child, str(Path(__file__).parent), str(tmp_path)],
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

    def never_execute(*a):
        pytest.fail("interrupted code executed again")

    from hashlib import sha256

    supervisor = Supervisor(
        tmp_path / "execution",
        coordinator,
        watchdog,
        never_execute,
        lambda state, task, raw: sha256(raw).hexdigest(),
    )
    resumed = Schedule(
        tmp_path / "schedule",
        supervisor,
        Policy((node.task,)),
        clock_epoch="test-boot",
        clock=lambda: 2000000000,
    )
    assert resumed.tick().mode == "blocked" and resumed.status().attempts == 2
    assert node.read().tasks[0].status == "unknown"
    assert node.read().budget.spent == 30


@pytest.mark.parametrize(
    "options",
    [
        {"tasks": ()},
        {"tasks": ("x", "x")},
        {"attempts_per_task": 0},
        {"total_attempts": 4097},
        {"duration_seconds": 3601},
        {"base_delay_ms": 0},
        {"max_delay_ms": 249},
    ],
)
def test_invalid_limits_rejected(options):
    with pytest.raises(Failure):
        Policy(**({"tasks": ("0:task",)} | options))
