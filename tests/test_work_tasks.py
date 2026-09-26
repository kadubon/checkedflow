"""Signed task ownership and conservative recovery, without executing candidate code."""

from hashlib import sha256

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import given, settings
from hypothesis import strategies as st
from test_operational_identity import setup

from checkedflow.core.authority import Credential
from checkedflow.core.operational import genesis
from checkedflow.core.values import Failure
from checkedflow.operational_codec import decode, state_bytes
from checkedflow.operational_identity import sign_command
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import Store
from checkedflow.wire import dumps, loads


class Harness:
    def __init__(self):
        self.keys, registry, self.template = setup()
        # Public deterministic fixture identity, not deployment key material.
        key = Ed25519PrivateKey.from_private_bytes(sha256(b"other-task-fixture").digest())
        self.keys[("other", 1)] = key
        registry[("other", 1)] = Credential(
            "other", 1, "b", "executor", "m", key.public_key().public_bytes_raw().hex(), 0
        )
        self.initial = genesis("operational-test", "m", tuple("abcd"), tuple(registry.values()))
        self.runtime = Runtime(self.initial)
        self.counter = 0
        self.events = []

    def send(self, kind, payload, actor="a", height=None):
        self.counter += 1
        state = self.runtime.state
        command = self.template | {
            "epoch": state.journal.epoch,
            "id": f"{state.journal.epoch}:c{self.counter}",
            "nonce": dict(state.journal.actors)[actor] + 1,
            "actor": actor,
            "kind": kind,
            "payload": {"mission": "m"} | payload,
        }
        signers = (
            {pair: key for pair, key in self.keys.items() if pair[0] in "abc"}
            if actor == "a"
            else {(actor, 1): self.keys[(actor, 1)]}
        )
        raw = sign_command(command, signers)
        height = state.height + 1 if height is None else height
        self.runtime.apply(raw, height=height)
        assert decode(state_bytes(self.runtime.state)) == self.runtime.state
        self.events.append((height, raw))
        return command["id"], raw

    def prepare(self, **changes):
        self.send("budget.configure", {"budget": 100, "verification_reserve": 20})
        self.send("mission.resume", {})
        ticket, _ = self.send(
            "budget.reserve", {"phase": "execute", "ceiling": 30, "target": "a" * 64}
        )
        task, _ = self.send(
            "task.admit",
            {
                "ticket": ticket,
                "workers": ["worker", "other"],
                "lease_blocks": 5,
                "expires": 100,
                "max_attempts": 2,
            }
            | changes,
        )
        return task, ticket


def test_signed_lifecycle_pause_completion_and_atomic_replay(tmp_path):
    h = Harness()
    task, ticket = h.prepare()
    h.send("task.lease", {"task": task}, "worker")
    h.send("task.start", {"task": task, "fence": 1}, "worker")
    h.send("task.heartbeat", {"task": task, "fence": 1}, "worker")
    h.send("mission.pause", {})
    h.send(
        "task.finish",
        {"task": task, "fence": 1, "outcome": "reported", "evidence": "b" * 64},
        "worker",
    )
    final = h.runtime.state
    assert final.tasks[0].status == "finished" and final.tasks[0].evidence == "b" * 64
    assert final.budget.spent == 30 and final.budget.reserved == 0
    store = Store(tmp_path / "tasks.sqlite", h.initial)
    previous = Runtime(h.initial).state_hash
    for height, raw in h.events:
        result = store.commit_block(height, [raw], previous_hash=previous)
        assert result.outcomes == ("OK",)
        previous = result.state_hash
    assert store.load() == final
    assert store.verify_history(expected_hash=h.runtime.state_hash) == final
    with pytest.raises(Failure, match="task-attached"):
        h.send("budget.settle", {"ticket": ticket, "outcome": "released", "charged": 0})


def test_competing_lease_stale_fence_and_prestart_attempt_limit():
    h = Harness()
    task, _ = h.prepare(lease_blocks=2)
    _, first = h.send("task.lease", {"task": task}, "worker")
    before = h.runtime.state_hash
    with pytest.raises(Failure, match="LEASE"):
        h.send("task.lease", {"task": task}, "other")
    with pytest.raises(Failure, match="FENCE"):
        h.send("task.start", {"task": task, "fence": 2}, "worker")
    assert h.runtime.state_hash == before
    h.runtime.tick(7)
    assert h.runtime.state.tasks[0].status == "ready"
    h.send("task.lease", {"task": task}, "other")
    h.runtime.apply(first, height=8)
    assert h.runtime.state.tasks[0].owner == "other" and h.runtime.state.tasks[0].fence == 2
    with pytest.raises(Failure, match="FENCE"):
        h.send("task.start", {"task": task, "fence": 1}, "worker")
    h.runtime.tick(10)
    assert h.runtime.state.tasks[0].status == "cancelled" and h.runtime.state.budget.spent == 0
    assert h.runtime.state.budget.available == 100


@pytest.mark.parametrize("trigger", ["expiry", "revoke", "cancel"])
def test_started_work_never_becomes_retryable_or_refundable(trigger):
    h = Harness()
    task, ticket = h.prepare()
    h.send("task.lease", {"task": task}, "worker")
    h.send("task.start", {"task": task, "fence": 1}, "worker")
    if trigger == "expiry":
        h.runtime.tick(10)
    elif trigger == "revoke":
        h.send("key.revoke", {"identity": "worker", "revision": 1, "reason": "compromise"})
    else:
        h.send("task.cancel", {"task": task})
    assert h.runtime.state.tasks[0].status == "unknown"
    assert h.runtime.state.budget.spent == 30 and h.runtime.state.budget.reserved == 0
    assert decode(state_bytes(h.runtime.state)) == h.runtime.state
    with pytest.raises(Failure, match="terminal"):
        h.send("task.lease", {"task": task}, "other")
    with pytest.raises(Failure, match="task-attached"):
        h.send("budget.settle", {"ticket": ticket, "outcome": "released", "charged": 0})


def test_drain_heartbeat_deadline_and_reported_unknown():
    h = Harness()
    task, _ = h.prepare(expires=12, lease_blocks=10)
    h.send("task.lease", {"task": task}, "worker")
    h.send("task.start", {"task": task, "fence": 1}, "worker")
    h.send("mission.drain", {})
    h.send("task.heartbeat", {"task": task, "fence": 1}, "worker")
    assert h.runtime.state.tasks[0].until == 12
    h.send(
        "task.finish",
        {"task": task, "fence": 1, "outcome": "unknown", "evidence": "b" * 64},
        "worker",
    )
    assert h.runtime.state.tasks[0].status == "unknown"
    assert h.runtime.state.budget.spent == 30


def test_unstarted_cancel_and_snapshot_corruption():
    h = Harness()
    task, _ = h.prepare()
    raw = state_bytes(h.runtime.state)
    for changes in [
        {"owner": "worker"},
        {"status": "finished"},
        {"ticket": "missing"},
        {"workers": ["a"]},
        {"reason": "invented"},
    ]:
        value = loads(raw)
        value["tasks"][0].update(changes)
        with pytest.raises(Failure):
            decode(dumps(value))
    h.send("task.cancel", {"task": task})
    assert h.runtime.state.budget.available == 100
    assert h.runtime.state.tasks[0].status == "cancelled"


def test_scope_purpose_admission_and_paused_dispatch_fail_closed():
    h = Harness()
    task, ticket = h.prepare(workers=["worker"])
    with pytest.raises(Failure, match="AUTHORITY"):
        h.send("task.lease", {"task": task}, "other")
    with pytest.raises(Failure, match="AUTHORITY"):
        h.send("task.lease", {"task": task})
    with pytest.raises(Failure, match="already attached"):
        h.send(
            "task.admit",
            {
                "ticket": ticket,
                "workers": ["worker"],
                "lease_blocks": 5,
                "expires": 100,
                "max_attempts": 2,
            },
        )
    h.send("mission.pause", {})
    with pytest.raises(Failure, match="PAUSED"):
        h.send("task.lease", {"task": task}, "worker")
    h.runtime.tick(100)
    assert h.runtime.state.tasks[0].status == "cancelled"


@settings(max_examples=35, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(
                ["lease", "start", "heartbeat", "finish", "pause", "resume", "drain", "cancel"]
            ),
            st.sampled_from(["worker", "other"]),
            st.integers(0, 3),
        ),
        max_size=12,
    )
)
def test_independent_sequence_model_preserves_fences_and_uncertainty(operations):
    h = Harness()
    task, _ = h.prepare(expires=20, lease_blocks=3)
    status, mode, owner, fence, until, spent = "ready", "running", "", 0, 0, 0
    for operation, actor, gap in operations:
        height = h.runtime.state.height + gap + 1
        h.runtime.tick(height)
        if status in {"ready", "leased", "running"} and (
            height >= 20 or (status != "ready" and height >= until)
        ):
            if status == "running":
                status, spent = "unknown", 30
            elif height >= 20 or fence == 2:
                status = "cancelled"
            else:
                status, owner, until = "ready", "", 0
        allowed = False
        payload = {"task": task}
        signer = actor
        kind = "task." + operation
        if operation in {"pause", "resume", "drain"}:
            kind, signer, payload, allowed = "mission." + operation, "a", {}, True
            mode = {"pause": "paused", "resume": "running", "drain": "draining"}[operation]
        elif operation == "cancel":
            signer = "a"
            allowed = status in {"ready", "leased", "running"}
            if allowed:
                if status == "running":
                    status, spent = "unknown", 30
                else:
                    status = "cancelled"
        elif operation == "lease":
            allowed = mode == "running" and status == "ready" and fence < 2
            if allowed:
                status, owner, fence, until = "leased", actor, fence + 1, min(height + 3, 20)
        else:
            payload["fence"] = max(1, fence)
            if operation == "start":
                allowed = mode == "running" and status == "leased" and owner == actor
                if allowed:
                    status = "running"
            elif operation == "heartbeat":
                allowed = mode != "paused" and status == "running" and owner == actor
                if allowed:
                    until = min(height + 3, 20)
            else:
                payload.update(outcome="reported", evidence="b" * 64)
                allowed = status == "running" and owner == actor
                if allowed:
                    status, spent = "finished", 30
        before = h.runtime.state_hash
        if allowed:
            h.send(kind, payload, signer, height)
        else:
            with pytest.raises(Failure):
                h.send(kind, payload, signer, height)
            assert h.runtime.state_hash == before
        actual = h.runtime.state.tasks[0]
        assert (actual.status, actual.fence, h.runtime.state.mode) == (status, fence, mode)
        assert h.runtime.state.budget.spent == spent
        assert h.runtime.state.budget.reserved == (
            30 if status in {"ready", "leased", "running"} else 0
        )


def test_empty_committed_blocks_charge_expired_started_work(tmp_path):
    h = Harness()
    task, _ = h.prepare()
    h.send("task.lease", {"task": task}, "worker")
    h.send("task.start", {"task": task, "fence": 1}, "worker")
    store = Store(tmp_path / "expiry.sqlite", h.initial)
    previous = Runtime(h.initial).state_hash
    for height, raw in h.events:
        previous = store.commit_block(height, [raw], previous_hash=previous).state_hash
    for height in range(7, 11):
        h.runtime.tick(height)
        previous = store.commit_block(height, [], previous_hash=previous).state_hash
    assert previous == h.runtime.state_hash
    assert store.verify_history(expected_hash=previous).tasks[0].status == "unknown"
    assert store.load().budget.spent == 30


def test_snapshot_cannot_claim_a_future_execution_start():
    h = Harness()
    task, _ = h.prepare()
    h.send("task.lease", {"task": task}, "worker")
    h.send("task.start", {"task": task, "fence": 1}, "worker")
    value = loads(state_bytes(h.runtime.state))
    value["tasks"][0]["started"] = h.runtime.state.height + 1
    with pytest.raises(Failure, match="future"):
        decode(dumps(value))
