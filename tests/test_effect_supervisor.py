"""Durable effects with signed node fixtures; no candidate execution or GitHub writes."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from test_effect_dispatch import fixture

from checkedflow.core.values import Failure
from checkedflow.effect_supervisor import MAX_OPERATIONS, Supervisor
from checkedflow.github_drafts import Outcome
from checkedflow.wire import document, dumps, loads
from checkedflow.worker_submission import Coordinator


class Executor:
    def __init__(self, path, monkeypatch, *, reserve=False, state=None):
        self.path = path
        self.f = fixture(path, monkeypatch, reserve=reserve)
        if state is not None:
            from checkedflow.operational_codec import decode
            from checkedflow.operational_runtime import Runtime

            self.f.h.runtime = Runtime(decode(state))
        self.sent = []
        self.behavior = "normal"
        self.coordinator = Coordinator(
            path / "commands",
            self.read,
            self.submit,
            self.f.h.keys[("effects", 1)],
            chain=self.read().chain,
            mission="m",
            actor="effects",
            revision=1,
        )
        self.supervisor = self.reopen()

    def read(self):
        return self.f.h.runtime.state

    def submit(self, raw):
        self.sent.append(raw)
        kind = document(raw)["command"]["kind"]
        if self.behavior == "before_" + kind:
            raise OSError("fixture disconnected before commit")
        self.f.h.runtime.apply(raw, height=self.read().height + 1)
        if self.behavior == "after_" + kind:
            raise OSError("fixture lost committed reply")
        return {}

    def reopen(self):
        return Supervisor(self.path / "executor", self.coordinator, self.f.dispatcher)

    def step(self):
        return self.supervisor.step(self.f.h.effect, self.f.intent, self.f.contract, self.f.inputs)

    @property
    def posts(self):
        return sum(method == "POST" for method, _ in self.f.calls)

    def evidence(self):
        return self.supervisor.observation(self.f.h.effect)

    def change(self, sql, arguments=()):
        with closing(sqlite3.connect(self.path / "executor/effects.sqlite")) as db, db:
            db.execute(sql, arguments)


def test_reserve_dispatch_publish_report_and_restart(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    assert e.evidence() is None
    assert e.step() == "observed"
    assert e.posts == 1 and len(e.sent) == 2
    report = document(e.evidence())
    assert report["outcome"] == "observed" and report["number"] == 7
    assert report["reason"] == "provider_confirmed"
    schema = loads(
        files("checkedflow").joinpath("data/effect-observation.schema.json").read_bytes()
    )
    plan_schema = loads(
        files("checkedflow").joinpath("data/github-draft-plan.schema.json").read_bytes()
    )
    assert schema["properties"]["plan"] == {
        key: value for key, value in plan_schema.items() if key not in {"$schema", "$id"}
    }
    Draft202012Validator(schema).validate(report)
    assert e.read().budget.spent == 40  # Three checks and the full effect ceiling.
    e.supervisor = e.reopen()
    assert e.step() == "observed" and e.posts == 1 and len(e.sent) == 2


@pytest.mark.parametrize("kind", ["effect.reserve", "effect.report"])
def test_lost_committed_reply_reconciles_original_bytes_without_resend(tmp_path, monkeypatch, kind):
    e = Executor(tmp_path, monkeypatch)
    e.behavior = "after_" + kind
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        e.step()
    original = e.coordinator.pending()
    assert original is not None and document(original)["command"]["kind"] == kind
    e.behavior = "normal"
    e.supervisor = e.reopen()
    assert e.step() == "observed"
    assert e.posts == 1 and e.sent.count(original) == 1 and len(e.sent) == 2


@pytest.mark.parametrize("kind", ["effect.reserve", "effect.report"])
def test_unconfirmed_submission_blocks_then_explicit_original_retransmission(
    tmp_path, monkeypatch, kind
):
    e = Executor(tmp_path, monkeypatch)
    e.behavior = "before_" + kind
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        e.step()
    original = e.coordinator.pending()
    before = (len(e.sent), e.posts)
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        e.step()
    assert (len(e.sent), e.posts) == before
    e.behavior = "normal"
    e.coordinator.retransmit()
    assert e.step() == "observed"
    assert e.posts == 1 and e.sent.count(original) == 2


def test_publication_failure_keeps_observation_without_provider_reexecution(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    original = e.f.store.put

    def unavailable(*args, **kwargs):
        raise OSError("fixture storage failed")

    monkeypatch.setattr(e.f.store, "put", unavailable)
    with pytest.raises(OSError):
        e.step()
    assert e.posts == 1 and document(e.evidence())["outcome"] == "observed"
    monkeypatch.setattr(e.f.store, "put", original)
    e.supervisor = e.reopen()
    assert e.step() == "observed" and e.posts == 1


def test_publication_acknowledgment_without_bytes_cannot_authorize_report(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    monkeypatch.setattr(e.f.store, "put", lambda *args, **kwargs: None)
    with pytest.raises(Failure):
        e.step()
    assert e.posts == 1 and len(e.sent) == 1
    assert e.f.h.current.status == "dispatch_reserved" and e.evidence() is not None


def test_crash_after_start_becomes_unknown_never_dispatches_again(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    calls = []

    def interrupted(*args):
        calls.append(True)
        raise SystemExit("fixture stopped after durable invocation claim")

    monkeypatch.setattr(e.f.dispatcher, "dispatch", interrupted)
    with pytest.raises(SystemExit):
        e.step()
    e.supervisor = e.reopen()
    assert e.step() == "unknown"
    assert calls == [True] and e.posts == 0
    assert document(e.evidence())["reason"] == "executor_interrupted"


def test_reservation_without_local_ownership_is_not_adopted_for_dispatch(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch, reserve=True)
    assert e.step() == "unknown" and e.posts == 0
    assert document(e.evidence())["reason"] == "unowned_reservation"


def test_another_reservation_cannot_fill_prepared_journal(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    e.f.provider.enabled = False
    with pytest.raises(Failure, match="DISABLED"):
        e.step()
    e.f.h.action("reserve")  # A separate signer caller, not this coordinator's saved request.
    e.f.provider.enabled = True
    with pytest.raises(Failure):
        e.step()
    assert not e.sent and not e.f.calls


@pytest.mark.parametrize(
    "outcome",
    [
        Outcome("unknown"),
        Outcome("wrong"),
        Outcome("confirmed", True),
        RuntimeError("private detail"),
    ],
)
def test_unknown_or_invalid_dispatch_is_retained_without_error_details(
    tmp_path, monkeypatch, outcome
):
    e = Executor(tmp_path, monkeypatch)

    def observe(*args):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(e.f.dispatcher, "dispatch", observe)
    assert e.step() == "unknown"
    assert b"private detail" not in e.evidence()
    assert e.read().budget.spent == 40


def test_pause_after_provider_observation_still_allows_signed_report(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    original = e.f.dispatcher.dispatch

    def pause(*args):
        result = original(*args)
        e.f.h.send("mission.pause", {})
        return result

    monkeypatch.setattr(e.f.dispatcher, "dispatch", pause)
    assert e.step() == "observed" and e.read().mode == "paused"


def test_expired_reporting_window_retains_evidence_for_governed_reconciliation(
    tmp_path, monkeypatch
):
    e = Executor(tmp_path, monkeypatch)
    original = e.f.dispatcher.dispatch

    def late(*args):
        result = original(*args)
        e.f.h.runtime.tick(e.f.h.current.until)
        return result

    monkeypatch.setattr(e.f.dispatcher, "dispatch", late)
    assert e.step() == "reconciliation_required"
    assert e.read().effects[0].status == "unknown" and e.posts == 1
    evidence = e.evidence()
    assert document(evidence)["number"] == 7
    assert e.step() == "reconciliation_required" and e.evidence() == evidence and e.posts == 1


def test_no_automatic_activation_of_unapproved_or_denied_effect(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    e.f.h.action("deny")
    assert e.step() == "denied" and e.evidence() is None
    assert not e.sent and not e.f.calls


def test_policy_withdrawal_before_reservation_prevents_charging(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    e.f.path.write_bytes(dumps(e.f.policy_record | {"enabled": False}))
    with pytest.raises(Failure, match="DISABLED"):
        e.step()
    assert e.f.h.current.status == "authorized" and not e.sent and not e.f.calls


def test_journal_argument_changes_and_cross_identity_are_rejected(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    assert e.step() == "observed"
    with pytest.raises(Failure, match="BINDING"):
        e.supervisor.step(
            e.f.h.effect,
            e.f.intent,
            e.f.contract,
            replace(e.f.inputs, base=replace(e.f.inputs.base, length=1)),
        )
    e.f.provider.actor = "other"
    with pytest.raises(Failure, match="SCOPE"):
        e.reopen()
    e.f.dispatcher.revision = 2
    with pytest.raises(Failure, match="SCOPE"):
        e.reopen()


def test_bounded_journal_does_not_evict_unknowns(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    for index in range(MAX_OPERATIONS):
        e.change(
            "INSERT INTO operations VALUES (?,?, 'dispatching', '', '', 0, NULL)",
            (str(index), b"fixture"),
        )
    with pytest.raises(Failure, match="CAPACITY"):
        e.step()
    assert not e.sent and not e.f.calls


def test_concurrent_executor_cannot_enter_during_provider_send(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    other = e.reopen()
    original = e.f.dispatcher.dispatch

    def concurrent(*args):
        with pytest.raises(Failure, match="BUSY"):
            other.step(e.f.h.effect, e.f.intent, e.f.contract, e.f.inputs)
        return original(*args)

    monkeypatch.setattr(e.f.dispatcher, "dispatch", concurrent)
    assert e.step() == "observed" and e.posts == 1


def test_prepared_local_intent_cannot_send_after_governed_denial(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    e.f.provider.enabled = False
    with pytest.raises(Failure):
        e.step()
    e.f.h.action("deny")
    assert e.step() == "reconciliation_required"
    assert not e.sent and not e.f.calls


def test_report_signer_outage_preserves_request_and_observation(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    original = e.coordinator._signer

    class Unavailable:
        def sign(self, message):
            if b'"kind":"effect.report"' in message:
                raise OSError("fixture signer unavailable")
            return original.sign(message)

    e.coordinator._signer = Unavailable()
    with pytest.raises(OSError):
        e.step()
    evidence = e.evidence()
    assert e.posts == 1 and e.coordinator.pending() is None
    with closing(sqlite3.connect(e.path / "executor/effects.sqlite")) as db:
        request = db.execute("SELECT report_request FROM operations").fetchone()[0]
    e.coordinator._signer = original
    assert e.step() == "observed" and e.posts == 1 and e.evidence() == evidence
    assert document(e.sent[-1])["command"]["id"] == request


@pytest.mark.parametrize(
    "change",
    [
        {"chain": "other"},
        {"executor": "standby"},
        {"number": True},
        {"outcome": []},
        {"outcome": "unknown", "number": 7},
        {"reason": "invented"},
        {"not_before_height": 0},
        {"extra": 1},
    ],
)
def test_corrupt_stored_observation_cannot_be_reported(tmp_path, monkeypatch, change):
    e = Executor(tmp_path, monkeypatch)
    assert e.step() == "observed"
    original = document(e.evidence())
    e.change("UPDATE operations SET evidence=?", (dumps(original | change),))
    with pytest.raises(Failure):
        e.step()
    assert e.posts == 1 and len(e.sent) == 2


@pytest.mark.parametrize(
    "sql,code",
    [
        ("DELETE FROM identity", "STORAGE"),
        ("CREATE TABLE foreign_table (value TEXT)", "VERSION"),
    ],
)
def test_incomplete_or_foreign_journal_is_not_reset(tmp_path, monkeypatch, sql, code):
    e = Executor(tmp_path, monkeypatch)
    e.change(sql)
    with pytest.raises(Failure, match=code):
        e.reopen()
    assert not e.sent


@pytest.mark.parametrize(
    "window", ["reserved", "before_send", "after_post", "publication", "reported"]
)
def test_process_exit_recovers_without_second_provider_invocation(tmp_path, monkeypatch, window):
    import os
    import subprocess
    import sys
    from pathlib import Path

    child = r"""
import os, sys
from pathlib import Path
import pytest
sys.path.insert(0, sys.argv[1])
from test_effect_supervisor import Executor
from checkedflow.operational_codec import state_bytes
from checkedflow.wire import document
root, window = Path(sys.argv[2]), sys.argv[3]
e = Executor(root, pytest.MonkeyPatch())
original_submit = e.coordinator._submit
def submit(raw):
    result = original_submit(raw)
    (root / "node.json").write_bytes(state_bytes(e.read()))
    kind = document(raw)["command"]["kind"]
    if ((window == "reserved" and kind == "effect.reserve")
        or (window == "reported" and kind == "effect.report")):
        (root / "pending.bin").write_bytes(raw)
        os._exit(39)
    return result
e.coordinator._submit = submit
original_request = e.f.provider._request
def request(method, suffix, **kwargs):
    result = original_request(method, suffix, **kwargs)
    if method == "POST":
        (root / "provider-called").write_text("once")
        if window == "after_post":
            os._exit(39)
    return result
e.f.provider._request = request
if window == "before_send":
    e.f.dispatcher.dispatch = lambda *args: os._exit(39)
if window == "publication":
    e.f.store.put = lambda *args, **kwargs: os._exit(39)
e.step()
raise AssertionError("crash window did not run")
"""
    result = subprocess.run(
        [sys.executable, "-c", child, str(Path(__file__).parent), str(tmp_path), window],
        capture_output=True,
        timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 39, result.stderr.decode(errors="replace")
    e = Executor(tmp_path, monkeypatch, state=(tmp_path / "node.json").read_bytes())
    expected = "unknown" if window in {"before_send", "after_post"} else "observed"
    assert e.step() == expected
    assert e.posts == (1 if window == "reserved" else 0)
    assert int((tmp_path / "provider-called").exists()) + e.posts <= 1
    if window == "reported":
        assert not e.sent and e.coordinator.pending() is None
