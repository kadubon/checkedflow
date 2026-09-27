"""Inherited dispatch requires current retained evidence, including after slow or concurrent I/O."""

from dataclasses import replace
from threading import Event, Thread

import pytest
from test_legacy_retention import local_configuration
from test_retention import ACCESS
from test_succession import fixture as succession_inputs

from checkedflow.core.values import Failure
from checkedflow.dispatch_watchdog import Watchdog
from checkedflow.legacy_retention import DispatchGuard


def prepared(tmp_path, *, supplied=True):
    path, _, raw, trusted, store, retained = local_configuration(tmp_path)
    state = replace(succession_inputs()[0]["successor"], mode="running")
    now = [0]

    def read():
        nonlocal state
        state = replace(state, height=state.height + 1)
        return state

    guard = DispatchGuard(path, raw, trusted)
    watchdog = Watchdog(
        read,
        chain=state.chain,
        mission=state.mission,
        max_read_age_ns=10,
        max_stall_ns=10,
        clock=lambda: now[0],
        retention=guard if supplied else None,
    )
    watchdog.poll()
    watchdog.poll()
    return watchdog, guard, store, retained, now


def test_inherited_dispatch_refuses_missing_and_released_evidence(tmp_path):
    watchdog, guard, store, retained, _ = prepared(tmp_path)
    assert watchdog.current().budget.inheritance is not None
    store.release(retained.pin, access=ACCESS)
    with pytest.raises(Failure, match="BINDING"):
        watchdog.current()
    with pytest.raises(Failure, match="NOT_READY"):
        watchdog.current()
    watchdog.poll()
    watchdog.poll()
    with pytest.raises(Failure, match="BINDING"):
        watchdog.current()
    # Independently supplied guard roots must bind the current inherited checkpoint.
    with pytest.raises(Failure, match="BINDING"):
        replace(guard, trusted=replace(guard.trusted, state_hash="0" * 64)).check(
            succession_inputs()[0]["successor"].budget.inheritance
        )


def test_inherited_dispatch_cannot_omit_guard(tmp_path):
    watchdog, _, _, _, _ = prepared(tmp_path, supplied=False)
    with pytest.raises(Failure, match="BINDING"):
        watchdog.current()


@pytest.mark.parametrize(
    "change,code", [("slow", "NOT_READY"), ("poll", "STALE"), ("stop", "STOPPED")]
)
def test_guard_completion_cannot_extend_or_replace_observation(tmp_path, monkeypatch, change, code):
    watchdog, _, _, _, now = prepared(tmp_path)

    def check(self, inherited):
        if change == "slow":
            now[0] = 10
        elif change == "poll":
            watchdog.poll()
        else:
            watchdog.stop()

    monkeypatch.setattr(DispatchGuard, "check", check)
    with pytest.raises(Failure, match=code):
        watchdog.current()


def test_stop_does_not_wait_for_retention_io(tmp_path, monkeypatch):
    watchdog, _, _, _, _ = prepared(tmp_path)
    entered, release = Event(), Event()
    results = []

    def check(self, inherited):
        entered.set()
        assert release.wait(5)

    def current():
        try:
            watchdog.current()
        except Failure as error:
            results.append(error.code)

    monkeypatch.setattr(DispatchGuard, "check", check)
    thread = Thread(target=current)
    thread.start()
    try:
        assert entered.wait(5)
        watchdog.stop()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and results == ["STOPPED"]
