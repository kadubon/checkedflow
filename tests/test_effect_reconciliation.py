"""Historical GET-only evidence followed by real signed administrative reconciliation."""

from dataclasses import replace
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from test_effect_supervisor import Executor
from test_github_drafts import pull

from checkedflow.core.values import Failure
from checkedflow.effect_reconciliation import Reconciler
from checkedflow.github_drafts import Outcome
from checkedflow.wire import document, loads


def setup(path, monkeypatch, *, rows=True, reserve=True):
    e = Executor(path, monkeypatch, reserve=reserve)
    f = e.f
    original = f.provider._request
    plan = f.intent._plan(f.h.current.operation, f.h.current.authorization)

    def read(method, suffix, **kw):
        assert method == "GET"
        result = original(method, suffix, **kw)
        return [pull(plan)] if suffix == "/pulls" and rows else result

    monkeypatch.setattr(f.provider, "_request", read)
    f.provider.enabled = False
    reconciler = Reconciler(
        e.coordinator, f.provider, f.store, f.dispatcher.access, f.dispatcher.policy
    )

    def collect():
        return reconciler.collect(f.h.effect, f.intent, f.contract, f.inputs.base, f.inputs.patch)

    return e, reconciler, collect


@pytest.mark.parametrize("condition", ["running", "pause", "expiry", "revoked", "withdrawn"])
def test_read_recovery_then_current_quorum_without_provider_writes(
    tmp_path, monkeypatch, condition
):
    e, _, collect = setup(tmp_path, monkeypatch)
    h = e.f.h
    if condition == "pause":
        h.send("mission.pause", {})
    elif condition == "expiry":
        h.runtime.tick(h.current.until)
    elif condition == "revoked":
        h.send("key.revoke", {"identity": "effects", "revision": 1, "reason": "compromise"})
    elif condition == "withdrawn":
        h.send("artifact.revoke", {"candidate": h.candidate})
    before = h.runtime.state_hash
    proposal = collect()
    assert h.runtime.state_hash == before and not e.sent and e.posts == 0
    assert not e.f.provider.journal.exists()
    assert e.f.store.get(proposal.evidence, access=e.f.dispatcher.access) == proposal.observation
    row = document(proposal.observation)
    assert row["outcome"] == "observed" and row["number"] == 7
    Draft202012Validator(
        loads(files("checkedflow").joinpath("data/effect-reconciliation.schema.json").read_bytes())
    ).validate(row)
    command = document(proposal.command)
    with pytest.raises(Failure):
        h.send(command["kind"], command["payload"], "effects")
    h.send(command["kind"], command["payload"])
    assert h.current.status == (
        "compensation_required" if condition == "withdrawn" else "reconciled"
    )
    assert h.current.number == 7 and h.current.evidence == proposal.evidence.digest
    assert h.runtime.state.budget.spent == 40 and not e.f.provider.enabled


def test_absence_retains_unknown_and_previous_object_never_authorizes_resend(tmp_path, monkeypatch):
    e, _, collect = setup(tmp_path, monkeypatch, rows=False)
    h = e.f.h
    h.action("reconcile", outcome="observed", number=7, evidence="a" * 64)
    for _ in range(2):
        proposal = collect()
        command = document(proposal.command)
        assert command["payload"]["number"] == 0
        h.send(command["kind"], command["payload"])
        assert h.current.status == "unknown" and h.current.number == 7
        with pytest.raises(Failure):
            h.action("reserve")
    assert not e.sent and e.posts == 0


@pytest.mark.parametrize("failure", [Failure("OUTCOME_UNKNOWN", "private"), OSError("private")])
def test_transport_failure_stays_unknown_without_private_diagnostics(
    tmp_path, monkeypatch, failure
):
    e, _, collect = setup(tmp_path, monkeypatch)

    def fail(*args, **kw):
        raise failure

    monkeypatch.setattr(e.f.provider, "_request", fail)
    proposal = collect()
    assert document(proposal.observation)["outcome"] == "unknown"
    assert b"private" not in proposal.observation and not e.sent


@pytest.mark.parametrize("change", ["unreserved", "intent", "reference", "actor", "scope"])
def test_changed_bindings_reject_before_provider_io(tmp_path, monkeypatch, change):
    e, reconciler, collect = setup(tmp_path, monkeypatch, reserve=change != "unreserved")
    if change == "intent":
        e.f.intent = replace(e.f.intent, result="a" * 64)
    elif change == "reference":
        e.f.inputs = replace(e.f.inputs, base=replace(e.f.inputs.base, manifest="b" * 64))
    elif change == "actor":
        e.coordinator.revision = 2
    elif change == "scope":
        reconciler.access = replace(reconciler.access, scopes=frozenset({"other"}))
    with pytest.raises(Failure):
        collect()
    assert not e.f.calls and not e.sent


@pytest.mark.parametrize("phase", ["read", "publication"])
def test_concurrent_governance_rejects_stale_proposal(tmp_path, monkeypatch, phase):
    e, _, collect = setup(tmp_path, monkeypatch)

    def change():
        e.f.h.action("reconcile", outcome="unknown", number=0, evidence="a" * 64)

    if phase == "read":
        e.f.hook = change
    else:
        original = e.f.store.put

        def changed(*args, **kwargs):
            original(*args, **kwargs)
            change()

        monkeypatch.setattr(e.f.store, "put", changed)
    with pytest.raises(Failure, match="CONFLICT"):
        collect()
    assert not e.sent and e.posts == 0


def test_publication_ack_without_readback_never_returns_proposal(tmp_path, monkeypatch):
    e, _, collect = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(e.f.store, "put", lambda *a, **kw: None)
    with pytest.raises(Failure):
        collect()
    assert not e.sent


@pytest.mark.parametrize("change", ["actor", "policy"])
def test_original_approved_provider_policy_is_required(tmp_path, monkeypatch, change):
    from checkedflow.wire import dumps

    e, _, collect = setup(tmp_path, monkeypatch)
    if change == "actor":
        e.f.provider.actor = "other"
    else:
        e.f.policy_record["intents"].append("f" * 64)
        e.f.path.write_bytes(dumps(e.f.policy_record))
    with pytest.raises(Failure, match="POLICY"):
        collect()
    assert not e.f.calls and not e.sent


@pytest.mark.parametrize(
    "outcome",
    [Outcome("bad"), Outcome("confirmed", True), Outcome("unknown", 1), Outcome("confirmed", 8)],
)
def test_malformed_or_changed_object_identity_is_rejected(tmp_path, monkeypatch, outcome):
    e, _, collect = setup(tmp_path, monkeypatch)
    e.f.h.action("reconcile", outcome="observed", number=7, evidence="a" * 64)
    monkeypatch.setattr(e.f.provider, "inspect_patch", lambda *a: outcome)
    with pytest.raises(Failure):
        collect()
    assert not e.sent


def test_missing_effect_and_corrupt_bytes_fail_closed(tmp_path, monkeypatch):
    e, reconciler, collect = setup(tmp_path, monkeypatch)
    with pytest.raises(Failure, match="NOT_FOUND"):
        reconciler.collect("missing", e.f.intent, e.f.contract, e.f.inputs.base, e.f.inputs.patch)
    original = e.f.store.get
    monkeypatch.setattr(
        e.f.store,
        "get",
        lambda ref, **kw: b"bad" if ref == e.f.inputs.base else original(ref, **kw),
    )
    with pytest.raises(Failure, match="INTEGRITY"):
        collect()
    assert not e.f.calls
