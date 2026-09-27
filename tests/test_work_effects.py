"""Signed effect eligibility, uncertainty, funding and durable replay; no provider writes."""

from hashlib import sha256
from importlib.resources import files

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from test_work_acceptance import Harness as AcceptanceHarness

from checkedflow.core.authority import Credential
from checkedflow.core.operational import genesis
from checkedflow.core.values import Failure
from checkedflow.core.work_effects import MAX_EFFECTS, validate
from checkedflow.operational_codec import decode, encode, state_bytes
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import Store
from checkedflow.wire import dumps, loads


class Harness(AcceptanceHarness):
    def __init__(self):
        super().__init__()
        credentials = list(self.initial.credentials)
        for name in ("effects", "standby"):
            key = Ed25519PrivateKey.from_private_bytes(sha256(name.encode()).digest())
            self.keys[(name, 1)] = key
            credentials.append(
                Credential(
                    name,
                    1,
                    "a",
                    "effect_executor",
                    "m",
                    key.public_key().public_bytes_raw().hex(),
                    0,
                )
            )
        self.initial = genesis(self.initial.chain, "m", tuple("abcd"), tuple(credentials))
        self.runtime = Runtime(self.initial)

    def prepare_effect(self, *, observations=4, **changes):
        self.prepare_checks()
        self.admit()
        for index in range(observations):
            self.observation(index)
        self.ticket, _ = self.send(
            "budget.reserve", {"phase": "execute", "ceiling": 10, "target": "d" * 64}
        )
        self.effect, _ = self.send(
            "effect.prepare",
            {
                "candidate": self.candidate,
                "ticket": self.ticket,
                "intent": "d" * 64,
                "policy": "e" * 64,
                "executor": "effects",
                "revision": 1,
                "expires": 500,
                "lease_blocks": 20,
            }
            | changes,
        )
        return self.effect

    def action(self, kind, **payload):
        actor = "effects" if kind in {"reserve", "report"} else "a"
        return self.send("effect." + kind, {"effect": self.effect} | payload, actor)

    @property
    def current(self):
        return self.runtime.state.effects[0]


def prepared():
    h = Harness()
    h.prepare_effect()
    return h


def reserved():
    h = prepared()
    h.action("authorize")
    h.action("reserve")
    return h


def report(h, outcome="observed", number=7):
    return h.action("report", fence=1, outcome=outcome, number=number, evidence="f" * 64)


def reconcile(h, outcome="observed", number=7):
    return h.action("reconcile", outcome=outcome, number=number, evidence="9" * 64)


def test_signed_effect_lifecycle_exact_replay_and_schema(tmp_path):
    h = prepared()
    assert not h.current.authorization and h.runtime.state.budget.reserved == 10
    h.action("authorize")
    _, raw = h.action("reserve")
    assert h.current.status == "dispatch_reserved" and h.current.fence == 1
    assert h.runtime.state.budget.spent == 50 and h.runtime.state.budget.reserved == 0
    state_hash = h.runtime.state_hash
    h.runtime.apply(raw, height=h.runtime.state.height)
    assert h.runtime.state_hash == state_hash
    h.send("mission.pause", {})
    report(h)
    assert h.current.status == "observed"
    with pytest.raises(Failure, match="UNRESOLVED"):
        h.send("mission.resume", {})
    reconcile(h)
    h.send("mission.resume", {})
    assert h.current.status == "reconciled" and h.current.number == 7
    schema = loads(files("checkedflow").joinpath("data/operational-state.schema.json").read_bytes())
    Draft202012Validator(schema).validate(encode(h.runtime.state))
    command_schema = loads(
        files("checkedflow").joinpath("data/effect-command.schema.json").read_bytes()
    )
    for _, envelope in h.events:
        command = loads(envelope)["command"]
        if command["kind"].startswith("effect."):
            Draft202012Validator(command_schema).validate(
                {k: command[k] for k in ("kind", "payload")}
            )
    store = Store(tmp_path / "effects.sqlite", h.initial)
    previous = Runtime(h.initial).state_hash
    for height, raw in h.events:
        result = store.commit_block(height, [raw], previous_hash=previous)
        assert result.outcomes == ("OK",)
        previous = result.state_hash
    assert store.verify_history(expected_hash=previous) == h.runtime.state
    assert store.load() == h.runtime.state


def test_unknown_reservation_cannot_retry_refund_or_resume():
    h = reserved()
    before = h.runtime.state_hash
    # A repeated live reservation must not reset its height window or reuse fence 1.
    with pytest.raises(Failure, match="STATE"):
        h.action("reserve")
    assert h.runtime.state_hash == before
    h.runtime.tick(h.current.until)
    assert h.current.status == "unknown" and h.current.reason == "dispatch_deadline"
    assert h.runtime.state.budget.spent == 50
    for action in ("reserve", "authorize", "deny"):
        with pytest.raises(Failure, match="STATE"):
            h.action(action)
    with pytest.raises(Failure, match="STATE"):
        report(h)
    with pytest.raises(Failure, match="BUDGET"):
        h.send("budget.settle", {"ticket": h.ticket, "outcome": "released", "charged": 0})
    h.send("mission.pause", {})
    with pytest.raises(Failure, match="UNRESOLVED"):
        h.send("mission.resume", {})
    reconcile(h, "unknown", 0)
    assert h.current.status == "unknown"
    reconcile(h)
    assert h.current.status == "reconciled" and h.runtime.state.budget.spent == 50
    h.send("mission.resume", {})


@pytest.mark.parametrize("authorized", [False, True])
def test_before_reservation_denial_or_expiry_only_releases_unused_funding(authorized):
    for cause in ("deny", "deadline", "candidate", "key"):
        h = prepared()
        if authorized:
            h.action("authorize")
        if cause == "deny":
            h.action("deny")
        elif cause == "deadline":
            h.runtime.tick(500)
        elif cause == "candidate":
            h.send("artifact.revoke", {"candidate": h.candidate})
        else:
            h.send("key.revoke", {"identity": "effects", "revision": 1, "reason": "compromise"})
        assert h.current.status == ("denied" if cause == "deny" else "expired")
        assert h.runtime.state.budget.spent == 40 and h.runtime.state.budget.reserved == 0
        assert decode(state_bytes(h.runtime.state)) == h.runtime.state


@pytest.mark.parametrize("cause", ["candidate", "key"])
def test_post_reservation_adverse_information_retains_unknown(cause):
    h = reserved()
    if cause == "candidate":
        h.send("artifact.revoke", {"candidate": h.candidate})
    else:
        h.send("key.revoke", {"identity": "effects", "revision": 1, "reason": "compromise"})
    assert h.current.status == "unknown" and h.runtime.state.budget.spent == 50
    reconcile(h)
    assert h.current.number == 7
    # A revoked executor cannot supply new reports; current quorum may inspect historical effects.
    assert h.current.status == ("compensation_required" if cause == "candidate" else "reconciled")


def test_reconciled_receipt_keeps_identity_after_uncertainty_or_withdrawal():
    h = reserved()
    report(h)
    reconcile(h)
    reconcile(h, "unknown", 0)
    assert h.current.status == "unknown" and h.current.number == 7
    with pytest.raises(Failure, match="CONFLICT"):
        reconcile(h, number=8)
    reconcile(h)
    h.send("artifact.withdraw", {"candidate": h.candidate, "task": h.checks[0]}, "v0")
    assert h.current.status == "compensation_required" and h.current.number == 7
    assert h.runtime.state.budget.spent == 50
    with pytest.raises(Failure, match="UNRESOLVED"):
        h.send("mission.resume", {})


def test_executor_scope_pause_and_fence_fail_closed():
    h = prepared()
    with pytest.raises(Failure, match="STATE"):
        h.action("reserve")
    h.send("mission.pause", {})
    with pytest.raises(Failure, match="PAUSED"):
        h.action("authorize")
    h.send("mission.resume", {})
    h.action("authorize")
    for actor in ("worker", "v0", "standby"):
        with pytest.raises(Failure, match="AUTHORITY"):
            h.send("effect.reserve", {"effect": h.effect}, actor)
    h.send("mission.drain", {})
    with pytest.raises(Failure, match="PAUSED"):
        h.action("reserve")
    h.send("mission.resume", {})
    h.action("reserve")
    with pytest.raises(Failure, match="FENCE"):
        h.action("report", fence=2, outcome="observed", number=7, evidence="f" * 64)
    for outcome, number in (("absent", 0), ("unknown", 7), ("observed", 0)):
        with pytest.raises(Failure):
            h.action("report", fence=1, outcome=outcome, number=number, evidence="f" * 64)
    report(h, "unknown", 0)
    assert h.current.status == "unknown"


def test_admission_requires_accepted_candidate_and_unused_intent_funding():
    for count in (0, 2):
        with pytest.raises(Failure, match="AUTHORITY"):
            Harness().prepare_effect(observations=count)
    h = prepared()
    payload = {
        "candidate": h.candidate,
        "ticket": h.ticket,
        "intent": "d" * 64,
        "policy": "e" * 64,
        "executor": "effects",
        "revision": 1,
        "expires": 500,
        "lease_blocks": 20,
    }
    with pytest.raises(Failure, match="DUPLICATE"):
        h.send("effect.prepare", payload)
    with pytest.raises(Failure, match="BUDGET"):
        h.send("effect.prepare", payload | {"intent": "c" * 64})
    with pytest.raises(Failure, match="BUDGET"):
        h.send(
            "task.admit",
            {
                "ticket": h.ticket,
                "workers": ["worker"],
                "lease_blocks": 5,
                "expires": 900,
                "max_attempts": 1,
            },
        )
    for executor in ("worker", "missing"):
        fresh = Harness()
        with pytest.raises(Failure, match="AUTHORITY"):
            fresh.prepare_effect(executor=executor)
    with pytest.raises(Failure, match="NOT_FOUND"):
        h.send("effect.authorize", {"effect": "missing"})
    for actor, code in (("effects", "AUTHORITY"), ("b", "QUORUM")):
        with pytest.raises(Failure, match=code):
            h.send("effect.authorize", {"effect": h.effect}, actor)


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation", "x"),
        ("intent", "0" * 64),
        ("policy", "x"),
        ("candidate", "missing"),
        ("ticket", "missing"),
        ("revision", 2),
        ("prepared", 0),
        ("expires", 1),
        ("lease_blocks", 10001),
        ("status", "sent"),
        ("authorization", ""),
        ("authorized", 0),
        ("fence", 0),
        ("reserved", 0),
        ("until", 1),
        ("number", -1),
        ("evidence", "x"),
        ("reason", 0),
    ],
)
def test_mutated_snapshots_cannot_create_effect_authority(field, value):
    h = reserved()
    record = encode(h.runtime.state)
    record["effects"][0][field] = value
    with pytest.raises(Failure):
        decode(dumps(record))


def test_retained_effect_pins_candidate_and_ticket():
    h = prepared()
    h.action("deny")
    h.send("artifact.revoke", {"candidate": h.candidate})
    h.send("mission.pause", {})
    h.send("journal.rollover", {})
    for tickets, candidates in (([h.ticket], []), ([], [h.candidate])):
        with pytest.raises(Failure, match="DEPENDENCY"):
            h.send(
                "history.archive",
                {
                    "expected_root": h.runtime.state.history.root,
                    "tickets": tickets,
                    "tasks": [],
                    "candidates": candidates,
                },
            )
    state = h.runtime.state
    with pytest.raises(Failure, match="CAPACITY"):
        validate(
            state.budget,
            state.effects * (MAX_EFFECTS + 1),
            state.candidates,
            state.tasks,
            state.credentials,
            state.mission,
            state.height,
        )


@settings(max_examples=20, deadline=None)
@given(st.lists(st.sampled_from(["unknown", "observed", "advance"]), min_size=1, max_size=12))
def test_observation_sequences_never_refund_or_reenable_dispatch(actions):
    h = reserved()
    for action in actions:
        if action == "advance":
            h.runtime.tick(h.runtime.state.height + 1)
        else:
            reconcile(h, action, 0 if action == "unknown" else 7)
        assert h.runtime.state.budget.spent == 50
        assert h.current.fence == 1 and h.current.status != "authorized"
        assert decode(state_bytes(h.runtime.state)) == h.runtime.state
