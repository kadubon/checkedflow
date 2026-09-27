"""Signed funded verification observations, including late adverse information."""

from dataclasses import replace
from hashlib import sha256
from importlib.resources import files

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from test_work_tasks import Harness as TaskHarness

from checkedflow.core.authority import Credential
from checkedflow.core.operational import genesis
from checkedflow.core.values import Failure
from checkedflow.core.work_acceptance import status, validate
from checkedflow.operational_codec import decode, encode, state_bytes
from checkedflow.operational_identity import prove_possession, sign_command
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import Store
from checkedflow.wire import dumps, loads


class Harness(TaskHarness):
    def __init__(self, organizations="abcd"):
        super().__init__()
        credentials = list(self.initial.credentials)
        for index, org in enumerate(organizations):
            identity = f"v{index}"
            key = Ed25519PrivateKey.from_private_bytes(sha256(identity.encode()).digest())
            self.keys[(identity, 1)] = key
            credentials.append(
                Credential(
                    identity, 1, org, "verifier", "m", key.public_key().public_bytes_raw().hex(), 0
                )
            )
        self.initial = genesis("operational-test", "m", tuple("abcd"), tuple(credentials))
        self.runtime = Runtime(self.initial)
        self.checks = []
        self.candidate = ""

    def prepare_checks(self, target="a" * 64):
        self.send("budget.configure", {"budget": 100, "verification_reserve": 40})
        self.send("mission.resume", {})
        for index in range(4):
            ticket, _ = self.send(
                "budget.reserve", {"phase": "verify", "ceiling": 10, "target": target}
            )
            task, _ = self.send(
                "task.admit",
                {
                    "ticket": ticket,
                    "workers": [f"v{index}"],
                    "lease_blocks": 50,
                    "expires": 1000,
                    "max_attempts": 1,
                },
            )
            self.checks.append(task)

    def admit(self, **changes):
        self.candidate, _ = self.send(
            "artifact.admit",
            {"target": "a" * 64, "artifact": "b" * 64, "expires": 1000, "checks": self.checks}
            | changes,
        )
        return self.candidate

    def observation(self, index, verdict="pass", outcome="reported"):
        task, actor = self.checks[index], f"v{index}"
        self.send("task.lease", {"task": task}, actor)
        self.send("task.start", {"task": task, "fence": 1}, actor)
        self.send(
            "task.finish",
            {"task": task, "fence": 1, "outcome": outcome, "evidence": "c" * 64},
            actor,
        )
        return self.send(
            "artifact.attest",
            {"candidate": self.candidate, "task": task, "evidence": "c" * 64, "verdict": verdict},
            actor,
        )

    def classification(self):
        state = self.runtime.state
        return status(state.candidates[0], state.credentials, state.height)


def prepared():
    h = Harness()
    h.prepare_checks()
    h.admit()
    return h


def test_three_organization_quorum_keeps_remaining_checks_and_replays(tmp_path):
    h = prepared()
    assert h.classification() == "pending"
    h.observation(0)
    h.observation(1)
    assert h.classification() == "pending"
    _, raw = h.observation(2)
    assert h.classification() == "accepted"
    before = h.runtime.state_hash
    h.runtime.apply(raw, height=h.runtime.state.height)
    assert h.runtime.state_hash == before
    assert h.runtime.state.budget.spent == 30 and h.runtime.state.budget.reserved == 10
    h.observation(3, "unknown", "unknown")
    assert h.classification() == "accepted" and h.runtime.state.budget.spent == 40
    store = Store(tmp_path / "acceptance.sqlite", h.initial)
    previous = Runtime(h.initial).state_hash
    for height, raw in h.events:
        result = store.commit_block(height, [raw], previous_hash=previous)
        assert result.outcomes == ("OK",)
        previous = result.state_hash
    assert store.load() == h.runtime.state
    assert store.verify_history(expected_hash=previous) == h.runtime.state
    schema = loads(files("checkedflow").joinpath("data/operational-state.schema.json").read_bytes())
    Draft202012Validator(schema).validate(encode(h.runtime.state))


def test_late_failure_quarantines_without_erasing_passing_quorum_or_costs():
    h = prepared()
    for index in range(3):
        h.observation(index)
    h.send("mission.pause", {})
    # Paused missions still accept already-produced adverse evidence, but cannot start new checks.
    h.send("mission.resume", {})
    h.observation(3, "fail")
    assert h.classification() == "quarantined"
    state = h.runtime.state
    assert [item.verdict for item in state.candidates[0].observations].count("pass") == 3
    assert state.budget.spent == 40 and state.budget.reserved == 0
    h.send("mission.pause", {})
    h.send("artifact.revoke", {"candidate": h.candidate})
    assert h.classification() == "revoked"


def test_withdrawal_and_compromise_differ_from_routine_retirement():
    h = prepared()
    for index in range(3):
        h.observation(index)
    state = h.runtime.state
    retired = tuple(
        replace(item, retired_height=state.height + 1) if item.identity == "v0" else item
        for item in state.credentials
    )
    assert status(state.candidates[0], retired, state.height + 2) == "accepted"
    assert (
        status(
            state.candidates[0],
            tuple(item for item in retired if item.identity != "v0"),
            state.height + 2,
        )
        == "quarantined"
    )
    h.send("key.revoke", {"identity": "v0", "revision": 1, "reason": "compromise"})
    assert h.classification() == "quarantined"
    h.send("mission.pause", {})
    h.send("artifact.withdraw", {"candidate": h.candidate, "task": h.checks[1]}, "v1")
    first = h.runtime.state.candidates[0].observations
    h.send("artifact.withdraw", {"candidate": h.candidate, "task": h.checks[1]}, "v1")
    assert h.runtime.state.candidates[0].observations == first
    assert any(item.withdrawn for item in first)


def test_expiry_and_adverse_evidence_after_expiry_remain_visible():
    h = prepared()
    for index in range(4):
        h.observation(index)
    h.runtime.tick(1000)
    assert h.classification() == "expired"
    h.send("artifact.withdraw", {"candidate": h.candidate, "task": h.checks[0]}, "v0")
    assert h.classification() == "quarantined"
    assert len(h.runtime.state.candidates[0].observations) == 4


def test_contradictory_verdict_is_retained_and_quarantines_even_without_failure():
    h = prepared()
    for index in range(3):
        h.observation(index)
    payload = {
        "candidate": h.candidate,
        "task": h.checks[0],
        "evidence": "c" * 64,
        "verdict": "unknown",
    }
    h.send("artifact.attest", payload, "v0")
    assert h.classification() == "quarantined"
    assert {
        item.verdict
        for item in h.runtime.state.candidates[0].observations
        if item.task == h.checks[0]
    } == {"pass", "unknown"}
    h.send("artifact.attest", payload | {"verdict": "fail"}, "v0")
    assert len(h.runtime.state.candidates[0].observations) == 5
    with pytest.raises(Failure, match="immutable"):
        h.send("artifact.attest", payload, "v0")
    assert h.runtime.state.budget.spent == 30 and h.runtime.state.budget.reserved == 10


def test_rotated_verifier_can_withdraw_historical_observation():
    h = prepared()
    for index in range(3):
        h.observation(index)
    state = h.runtime.state
    key = Ed25519PrivateKey.from_private_bytes(sha256(b"rotated-verifier-fixture").digest())
    command = h.template | {
        "epoch": 0,
        "id": "0:rotate-verifier",
        "nonce": dict(state.journal.actors)["a"] + 1,
        "kind": "key.schedule",
        "payload": {
            "mission": "m",
            "identity": "v0",
            "revision": 2,
            "public_key": key.public_key().public_bytes_raw().hex(),
            "activation_height": state.height + 2,
            "proof": "",
        },
    }
    administrators = {pair: signer for pair, signer in h.keys.items() if pair[0] in "abc"}
    h.runtime.apply(
        sign_command(prove_possession(command, key), administrators), height=state.height + 1
    )
    h.runtime.tick(state.height + 2)
    assert h.classification() == "accepted"
    withdrawal = command | {
        "id": "0:rotated-withdrawal",
        "actor": "v0",
        "revision": 2,
        "nonce": dict(state.journal.actors)["v0"] + 1,
        "kind": "artifact.withdraw",
        "payload": {"mission": "m", "candidate": h.candidate, "task": h.checks[0]},
    }
    h.runtime.apply(sign_command(withdrawal, {("v0", 2): key}), height=state.height + 3)
    assert h.classification() == "quarantined"
    assert all(item.revision == 1 for item in h.runtime.state.candidates[0].observations)
    assert decode(state_bytes(h.runtime.state)) == h.runtime.state


def test_funded_check_scope_and_distinct_organizations_are_mandatory():
    h = Harness(organizations="aaab")
    h.prepare_checks()
    with pytest.raises(Failure, match="QUORUM"):
        h.admit()
    h = Harness()
    h.prepare_checks()
    for changes in (
        {"checks": h.checks[:3]},
        {"checks": h.checks[:3] + ["absent"]},
        {"target": "d" * 64},
        {"artifact": "BAD"},
        {"expires": 1},
    ):
        with pytest.raises(Failure):
            h.admit(**changes)
    h.send("mission.pause", {})
    with pytest.raises(Failure, match="PAUSED"):
        h.admit()
    h.send("mission.resume", {})
    h.admit()
    with pytest.raises(Failure, match="already used"):
        h.admit()


def test_verifier_cannot_forge_owner_target_evidence_or_success():
    h = prepared()
    payload = {
        "candidate": h.candidate,
        "task": h.checks[0],
        "evidence": "c" * 64,
        "verdict": "pass",
    }
    for changes, actor in (
        ({}, "v0"),
        ({"task": "absent"}, "v0"),
        ({"candidate": "absent"}, "v0"),
        ({}, "worker"),
    ):
        with pytest.raises(Failure):
            h.send("artifact.attest", payload | changes, actor)
    task = h.checks[0]
    h.send("task.lease", {"task": task}, "v0")
    with pytest.raises(Failure, match="no observation"):
        h.send("artifact.withdraw", {"candidate": h.candidate, "task": task}, "v0")
    with pytest.raises(Failure, match="completed check"):
        h.send("artifact.attest", payload, "v0")
    h.send("task.start", {"task": task, "fence": 1}, "v0")
    h.send(
        "task.finish", {"task": task, "fence": 1, "outcome": "unknown", "evidence": "c" * 64}, "v0"
    )
    with pytest.raises(Failure, match="completed check"):
        h.send("artifact.attest", payload, "v0")
    with pytest.raises(Failure, match="unsupported observation"):
        h.send("artifact.attest", payload | {"verdict": "true"}, "v0")
    with pytest.raises(Failure, match="evidence differs"):
        h.send("artifact.attest", payload | {"verdict": "unknown", "evidence": "d" * 64}, "v0")
    h.send("artifact.attest", payload | {"verdict": "unknown"}, "v0")
    with pytest.raises(Failure, match="immutable"):
        h.send("artifact.attest", payload | {"verdict": "unknown"}, "v0")
    assert h.classification() == "pending"


@settings(max_examples=16, deadline=None)
@given(
    st.lists(st.sampled_from(["pass", "fail", "unknown"]), min_size=4, max_size=4),
    st.permutations((0, 1, 2, 3)),
)
def test_observation_order_matches_independent_reference(verdicts, order):
    h = prepared()
    seen = []
    for index in order:
        h.observation(index, verdicts[index])
        seen.append(verdicts[index])
        expected = (
            "quarantined"
            if "fail" in seen
            else ("accepted" if seen.count("pass") >= 3 else "pending")
        )
        assert h.classification() == expected
        assert h.runtime.state.budget.spent == 10 * len(seen)
        assert h.runtime.state.budget.reserved == 40 - 10 * len(seen)


def test_snapshot_rejects_forged_observations_and_shared_checks(monkeypatch):
    h = prepared()
    h.observation(0)
    state = h.runtime.state
    source = encode(state)
    for field, value in {
        "actor": "v1",
        "revision": 5,
        "organization": "b",
        "height": 0,
        "evidence": "e" * 64,
        "withdrawn": 1,
        "verdict": "PASS",
    }.items():
        changed = loads(dumps(source))
        changed["candidates"][0]["observations"][0][field] = value
        with pytest.raises(Failure):
            decode(dumps(changed))
    candidate = state.candidates[0]
    for candidates in (
        (candidate, candidate),
        (replace(candidate, checks=tuple(reversed(candidate.checks))),),
        (candidate, replace(candidate, identity="z")),
        (replace(candidate, observations=candidate.observations * 2),),
    ):
        with pytest.raises(Failure):
            validate(
                candidates,
                state.budget,
                state.tasks,
                state.credentials,
                state.mission,
                state.height,
            )
    monkeypatch.setattr("checkedflow.core.work_acceptance.MAX_CANDIDATES", 1)
    with pytest.raises(Failure, match="CAPACITY"):
        h.admit()
    with pytest.raises(Failure, match="CAPACITY"):
        validate(
            (candidate, candidate),
            state.budget,
            state.tasks,
            state.credentials,
            state.mission,
            state.height,
        )
