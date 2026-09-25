from __future__ import annotations

from copy import deepcopy

import pytest
from conftest import Harness
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from checkedflow.core.machine import accounting, advance, replay
from checkedflow.core.model import BlockHeight, genesis
from checkedflow.core.values import Failure, array, fields, integer, names, obj, text
from checkedflow.identity import authenticate, sign
from checkedflow.runtime import Runtime
from checkedflow.serialization import decode, encode
from checkedflow.wire import dumps, loads


def test_full_lifecycle_preserves_roles_and_funded_verification(h):
    h.task()
    s = h.propose()
    assert s.missions["m"].spent == 2
    assert s.missions["m"].reserved == 4
    for index in range(2):
        assert h.vote(index=index).capabilities["c"].status == "candidate"
    s = h.vote(index=2)
    assert s.capabilities["c"].status == "checked"
    assert s.missions["m"].reserved == 1
    h.task("t2", dependencies=("c",))
    assert accounting(h.runtime.state, "m")["reuse_calls"] == 1
    h.propose("c2", "t2")
    assert h.runtime.state.capabilities["c2"].depth == 2
    for i in range(3):
        h.vote("c2", i)
    report = accounting(h.runtime.state, "m")
    assert report["unique_generated_behaviors"] == 1
    assert report["copies"] == 1
    assert report["causal_acceleration"] is None
    assert decode(loads(dumps(encode(h.runtime.state)))) == h.runtime.state


def test_signed_replay_and_idempotency(h):
    h.checked()
    replayed = Runtime(h.initial)
    core_events = []
    for envelope, height in h.events:
        command, context = authenticate(replayed.state, envelope, height)
        core_events.append((command, context))
        replayed.apply(envelope, height=height)
    assert replayed.state_hash == h.runtime.state_hash
    assert replay(h.initial, core_events) == h.runtime.state
    last, height = h.events[-1]
    assert h.runtime.apply(last, height=height) == replayed.state
    changed = deepcopy(last)
    changed["command"]["payload"]["result"]["evidence"]["reason"] = "changed"
    with pytest.raises(Failure, match="SIGNATURE"):
        h.runtime.apply(changed, height=height)
    resigned = sign(changed["command"], {"w2": h.keys["w2"]})
    with pytest.raises(Failure, match="CONFLICT"):
        h.runtime.apply(resigned, height=height)


def test_candidate_must_match_signed_generation_receipt(h):
    h.task()
    before = h.runtime.state_hash
    with pytest.raises(Failure, match="BINDING"):
        h.propose(operations=("increment",))
    assert h.runtime.state_hash == before


def test_expiry_keeps_cost_and_requires_reconciliation(h):
    h.task(start=True, finish=False, effect="external")
    deadline = h.runtime.state.tasks["t"].deadline
    s = h.runtime.tick(deadline)
    assert s.tasks["t"].status == "uncertain"
    assert s.missions["m"].spent == 2 and s.missions["m"].reserved == 0
    assert s.residuals["timeout:t:1"].status == "open"
    with pytest.raises(Failure, match="LEASE"):
        h.apply("task.lease", {"id": "t"})
    h.apply(
        "task.reconcile", {"id": "t", "retry": True, "reason": "actuator reconciled"}, admin=True
    )
    h.apply("task.lease", {"id": "t"})
    assert h.runtime.state.tasks["t"].fence == 2
    with pytest.raises(Failure, match="FENCE"):
        h.apply("task.start", {"id": "t", "fence": 1})
    assert "timeout:t:1" in h.runtime.state.residuals


def test_pure_replay_includes_empty_block_expiry(h):
    h.checked()
    h.task("pending", finish=False)
    events = []
    runtime = Runtime(h.initial)
    for envelope, height in h.events:
        events.append(authenticate(runtime.state, envelope, height))
        runtime.apply(envelope, height=height)
    events.append(BlockHeight(9000))
    expected = runtime.tick(9000)
    assert expected.capabilities["c"].status == "expired"
    assert expected.tasks["pending"].status == "uncertain"
    assert replay(h.initial, events) == expected
    assert h.initial.height == 0


def test_shared_ancestors_are_checked_once(h, monkeypatch):
    from checkedflow.core import machine

    state = h.checked()
    # A bounded synthetic DAG isolates traversal cost from signature processing.
    for i in range(1, 30):
        cap = deepcopy(state.capabilities["c"])
        cap.dependencies = tuple(list(state.capabilities)[-8:])
        state.capabilities[f"c{i}"] = cap
    inspected = []
    original = machine.require

    def counted(condition, code, message):
        if message == "unknown capability":
            inspected.append(code)
        original(condition, code, message)

    monkeypatch.setattr(machine, "require", counted)
    machine._eligible(state, "m", ("c29",))
    assert len(inspected) == len(state.capabilities)
    state.capabilities["c"].status = "revoked"
    with pytest.raises(Failure, match="DEPENDENCY"):
        machine._eligible(state, "m", ("c29",))


def test_negative_verification_blocks_and_releases_unstarted_reservations(h):
    h.task()
    h.propose()
    s = h.vote(outcome="fail")
    assert s.capabilities["c"].status == "quarantined"
    assert s.missions["m"].spent == 3 and s.missions["m"].reserved == 0
    assert any(r.subject == "c" for r in s.residuals.values())
    with pytest.raises(Failure, match="DEPENDENCY"):
        h.task("uses-bad", dependencies=("c",))


def test_withdrawal_propagates_and_never_rewrites_old_state(h):
    before = h.checked()
    h.task("child-task", dependencies=("c",))
    h.propose("child", "child-task")
    s = h.apply("capability.revoke", {"id": "c", "reason": "dependency incident"}, admin=True)
    assert s.capabilities["c"].status == "revoked"
    assert s.capabilities["child"].status == "quarantined"
    assert before.capabilities["c"].status == "checked"
    assert accounting(s, "m")["withdrawn_artifacts"] == 2


def test_unknown_novelty_does_not_count(h):
    other = Harness(exhaustive=False)
    other.checked()
    report = accounting(other.runtime.state, "m")
    assert report["unknown_novelty"] == 1
    assert report["unique_generated_behaviors"] == 0


def test_external_origin_remains_separate(h):
    h.task()
    h.propose(origin="external")
    for i in range(3):
        h.vote(index=i)
    assert accounting(h.runtime.state, "m")["external_artifacts"] == 1
    assert accounting(h.runtime.state, "m")["unique_generated_behaviors"] == 0


def test_key_revocation_quarantines_supported_capabilities(h):
    h.checked()
    h.apply("worker.revoke", {"id": "w1", "reason": "compromised signing key"}, admin=True)
    assert h.runtime.state.capabilities["c"].status == "quarantined"
    envelope = h.envelope("task.lease", {"id": "x"}, actor="w1")
    with pytest.raises(Failure, match="SIGNATURE"):
        h.runtime.apply(envelope, height=h.runtime.state.height + 1)


@pytest.mark.parametrize(
    "change,code",
    [
        ({"api_version": "future"}, "SCHEMA"),
        ({"nonce": True}, "SCHEMA"),
        ({"nonce": 999}, "NONCE"),
        ({"chain": "other"}, "CHAIN"),
        ({"kind": "execute-anything"}, "SCHEMA"),
        ({"extra": None}, "SCHEMA"),
    ],
)
def test_rejected_envelope_is_atomic(h, change, code):
    envelope = h.envelope("task.lease", {"id": "missing"})
    envelope = sign(envelope["command"] | change, {"w0": h.keys["w0"]})
    before = h.runtime.state_hash
    with pytest.raises(Failure, match=code):
        h.runtime.apply(envelope, height=h.runtime.state.height + 1)
    assert before == h.runtime.state_hash


def test_administration_cannot_be_self_authorized(h):
    envelope = h.envelope("worker.revoke", {"id": "w0", "reason": "self-grant"})
    with pytest.raises(Failure, match="QUORUM"):
        h.runtime.apply(envelope, height=h.runtime.state.height + 1)
    bad = h.envelope("worker.revoke", {"id": "w0", "reason": "duplicated quorum"}, admin=True)
    bad["signatures"] = [bad["signatures"][0]] * 3
    with pytest.raises(Failure, match="SIGNATURE"):
        h.runtime.apply(bad, height=h.runtime.state.height + 1)
    insufficient = h.envelope(
        "worker.revoke", {"id": "w0", "reason": "only two approvals"}, admin=True
    )
    insufficient["signatures"] = insufficient["signatures"][:2]
    with pytest.raises(Failure, match="QUORUM"):
        h.runtime.apply(insufficient, height=h.runtime.state.height + 1)


@pytest.mark.parametrize(
    "mission",
    [
        {"budget": 3, "verification_reserve": 4},
        {"workers": ["missing"]},
        {"verifier": "missing"},
        {"contract": "other"},
        {"expires": 1},
        {"image": "python:latest"},
        {"max_depth": 17},
        {"workers": ["w0"]},
    ],
)
def test_mission_boundaries(h, mission):
    with pytest.raises(Failure):
        h.apply("mission.create", Harness.mission(id="bad") | mission, admin=True)


def test_capacity_reserves_verification_first():
    protected = Harness(budget=10)
    protected.task("would-starve-verification", cost=7, start=False)
    with pytest.raises(Failure, match="BUDGET"):
        protected.apply("task.lease", {"id": "would-starve-verification"})
    assert protected.runtime.state.missions["m"].reserved == 0
    h = Harness(budget=10)
    h.task(cost=6)
    h.propose()
    assert h.runtime.state.missions["m"].reserved == 4
    h.task("blocked", cost=1, start=False)
    with pytest.raises(Failure, match="BUDGET"):
        h.apply("task.lease", {"id": "blocked"})
    for i in range(4):
        h.vote(index=i)
    assert accounting(h.runtime.state, "m")["available"] == 0


def test_scope_and_lifecycle_guards(h):
    h.checked()
    h.apply("mission.create", Harness.mission(id="other", receiver="different"), admin=True)
    with pytest.raises(Failure, match="SCOPE"):
        h.apply(
            "task.create",
            {
                "id": "x",
                "mission": "other",
                "phase": "execute",
                "spec": {},
                "dependencies": ["c"],
                "cost": 1,
                "ttl": 30,
                "effect": "isolated",
            },
        )
    expired = advance(h.runtime.state, 9000)
    assert expired.capabilities["c"].status == "expired"
    with pytest.raises(Failure, match="HEIGHT"):
        advance(expired, 8999)


def test_dependencies_cannot_reference_forward_or_rewrite_a_parent(h):
    with pytest.raises(Failure, match="DEPENDENCY"):
        h.task(dependencies=("future-self",), start=False)
    h.checked()
    h.task("child", dependencies=("c",))
    before = h.runtime.state_hash
    with pytest.raises(Failure, match="CONFLICT"):
        h.propose("c", "child")
    assert before == h.runtime.state_hash


@pytest.mark.parametrize("value", [True, -1, 1.2, None, "1", 9007199254740992])
def test_integer_boundary(value):
    with pytest.raises(Failure):
        integer(value)


def test_value_boundaries_and_genesis(h):
    for fn, value in [(obj, []), (text, ""), (array, {}), (names, ["x", "x"])]:
        with pytest.raises(Failure):
            fn(value)
    with pytest.raises(Failure):
        fields({"x": 1}, "y")
    with pytest.raises(Failure):
        genesis("x", {"x": "a" * 64})
    for orgs in [{str(i): "a" * 64 for i in range(4)}, {str(i): str(i) for i in range(4)}]:
        with pytest.raises(Failure):
            genesis("x", orgs)


class BudgetMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.h = Harness(budget=100)
        self.next_id = 0
        self.expected_spent = 0

    @rule(cost=st.integers(1, 20))
    def attempt(self, cost):
        identity = f"t{self.next_id}"
        self.next_id += 1
        self.h.task(identity, cost=cost, start=False)
        before = self.h.runtime.state_hash
        if cost <= 100 - self.expected_spent - 4:
            self.h.apply("task.lease", {"id": identity})
            self.h.apply("task.start", {"id": identity, "fence": 1})
            self.h.apply(
                "task.finish",
                {"id": identity, "fence": 1, "result": {"outcome": "reported", "evidence": {}}},
            )
            self.expected_spent += cost
        else:
            with pytest.raises(Failure, match="BUDGET"):
                self.h.apply("task.lease", {"id": identity})
            assert self.h.runtime.state_hash == before

    @invariant()
    def independent_budget_model(self):
        mission = self.h.runtime.state.missions["m"]
        assert mission.spent == self.expected_spent
        assert mission.reserved == 0
        assert mission.spent <= 96


TestBudgetMachine = BudgetMachine.TestCase
TestBudgetMachine.settings = settings(max_examples=15, stateful_step_count=15, deadline=None)
