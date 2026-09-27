"""Process-local inhibition with controlled clocks; no consensus qualification claim."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest
from test_operational_runtime import runtime_and_command

from checkedflow.core.values import Failure
from checkedflow.dispatch_watchdog import Watchdog


class Fixture:
    def __init__(self):
        runtime, _, _ = runtime_and_command()
        self.state = replace(runtime.state, height=1, mode="running")
        self.now = 100
        self.error = None
        self.duration = 0
        self.watchdog = Watchdog(
            self.read,
            chain=self.state.chain,
            mission=self.state.mission,
            max_read_age_ns=10,
            max_stall_ns=20,
            clock=lambda: self.now,
        )

    def read(self):
        self.now += self.duration
        if self.error:
            raise self.error
        return self.state

    def tick(self, height, time):
        self.state = replace(self.state, height=height)
        self.now = time
        return self.watchdog.poll()

    def ready(self):
        self.watchdog.poll()
        self.tick(2, 101)
        assert self.watchdog.current() == self.state


def denied(watchdog, code="NOT_READY"):
    with pytest.raises(Failure, match=code):
        watchdog.current()


def test_cold_start_read_expiry_and_restart_require_progress():
    f = Fixture()
    denied(f.watchdog)
    f.watchdog.poll()
    denied(f.watchdog)
    f.tick(1, 101)
    denied(f.watchdog)
    f.tick(2, 102)
    assert f.watchdog.current() == f.state
    f.now = 112
    denied(f.watchdog)
    f.tick(3, 113)
    denied(f.watchdog)
    f.tick(4, 114)
    assert f.watchdog.current() == f.state
    restarted = Watchdog(
        f.read,
        chain=f.state.chain,
        mission="m",
        max_read_age_ns=10,
        max_stall_ns=20,
        clock=lambda: f.now,
    )
    restarted.poll()
    denied(restarted)


def test_identical_successful_reads_do_not_hide_quorum_stall():
    f = Fixture()
    f.ready()
    for time in (105, 110, 115, 120):
        f.tick(2, time)
        assert f.watchdog.current() == f.state
    f.now = 121
    denied(f.watchdog)
    f.tick(3, 122)
    denied(f.watchdog)
    f.tick(4, 123)
    assert f.watchdog.current() == f.state


@pytest.mark.parametrize("mode", ["paused", "draining"])
def test_maintenance_and_failure_break_the_observation_sequence(mode):
    f = Fixture()
    f.ready()
    f.state = replace(f.state, mode=mode)
    f.tick(3, 102)
    denied(f.watchdog)
    f.state = replace(f.state, mode="running")
    f.tick(4, 103)
    denied(f.watchdog)
    f.tick(5, 104)
    assert f.watchdog.current() == f.state
    f.error = OSError("own-node connection lost")
    with pytest.raises(OSError):
        f.watchdog.poll()
    denied(f.watchdog)
    f.error = None
    f.tick(6, 105)
    denied(f.watchdog)
    f.tick(7, 106)
    assert f.watchdog.current() == f.state


@pytest.mark.parametrize(
    "mutation,code",
    [
        ({"height": 1}, "CONFLICT"),
        ({"mode": "paused"}, "CONFLICT"),
        ({"chain": "another"}, "SCOPE"),
        ({"mission": "another"}, "SCOPE"),
        ({"profile": "future"}, "VERSION"),
        ({"height": True}, "SHAPE"),
    ],
)
def test_invalid_observation_never_leaves_old_readiness(mutation, code):
    f = Fixture()
    f.ready()
    f.state = replace(f.state, **mutation)
    with pytest.raises(Failure, match=code):
        f.watchdog.poll()
    denied(f.watchdog, "STOPPED" if code == "CONFLICT" else "NOT_READY")


def test_slow_reads_use_request_start_and_cannot_refresh_readiness():
    f = Fixture()
    f.ready()
    f.duration = 10
    with pytest.raises(Failure, match="STALE"):
        f.tick(3, 102)
    denied(f.watchdog)
    f.duration = 2
    f.tick(4, 113)
    denied(f.watchdog)
    f.tick(5, 116)
    assert f.watchdog.current() == f.state
    f.now = 126
    denied(f.watchdog)


def test_crossing_age_limit_during_read_resets_warmup():
    f = Fixture()
    f.ready()
    f.duration = 2
    f.tick(3, 110)
    denied(f.watchdog)


@pytest.mark.parametrize("now", [99, -1, True, 1.0])
def test_clock_regression_or_invalid_value_latches_stop(now):
    f = Fixture()
    f.ready()
    f.now = now
    with pytest.raises(Failure, match="CLOCK"):
        f.watchdog.current()
    f.now = 200
    denied(f.watchdog, "STOPPED")
    with pytest.raises(Failure, match="STOPPED"):
        f.watchdog.poll()


def test_stop_and_readiness_are_not_blocked_by_an_outstanding_read():
    f = Fixture()
    f.ready()
    entered, finish = Event(), Event()

    def read():
        entered.set()
        assert finish.wait(5)
        return replace(f.state, height=3)

    f.watchdog._read = read
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(f.watchdog.poll)
        try:
            assert entered.wait(5)
            denied(f.watchdog)
            with pytest.raises(Failure, match="BUSY"):
                f.watchdog.poll()
            f.watchdog.stop()
            denied(f.watchdog, "STOPPED")
        finally:
            finish.set()
        with pytest.raises(Failure, match="STOPPED"):
            future.result(timeout=5)
    denied(f.watchdog, "STOPPED")


@pytest.mark.parametrize("age,stall", [(0, 1), (True, 1), (1, 0), (1, 3_600_000_000_001)])
def test_duration_policy_must_be_explicit_and_bounded(age, stall):
    with pytest.raises(Failure):
        Watchdog(lambda: None, chain="c", mission="m", max_read_age_ns=age, max_stall_ns=stall)
