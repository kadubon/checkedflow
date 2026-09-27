"""Application-key lifecycle under current quorum, exact possession proofs and replay."""

from dataclasses import replace
from hashlib import sha256
from importlib.resources import files

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from test_operational_identity import setup
from test_operational_runtime import runtime_and_command

from checkedflow.core.key_registry import MAX_REVISIONS, change, validate
from checkedflow.core.values import Failure
from checkedflow.operational_codec import decode, state_bytes
from checkedflow.operational_identity import (
    authenticate,
    possession_message,
    prove_possession,
    sign_command,
)
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import Store
from checkedflow.wire import loads


def schedule(command, *, identity="a", revision=2, activation=4):
    key = Ed25519PrivateKey.from_private_bytes(sha256(f"{identity}:{revision}".encode()).digest())
    proposal = command | {
        "kind": "key.schedule",
        "payload": {
            "mission": "m",
            "identity": identity,
            "revision": revision,
            "public_key": key.public_key().public_bytes_raw().hex(),
            "activation_height": activation,
            "proof": "",
        },
    }
    return prove_possession(proposal, key), key


def test_pending_activation_retirement_and_nonce_continuity():
    runtime, command, keys = runtime_and_command()
    proposal, successor = schedule(command)
    raw = sign_command(proposal, keys)
    runtime.apply(raw, height=1)
    assert decode(state_bytes(runtime.state)) == runtime.state
    assert len(runtime.state.credentials) == 6
    # Duplicate proposal acknowledges the receipt without scheduling a second revision.
    runtime.apply(raw, height=2)
    assert len(runtime.state.credentials) == 6
    rotated_keys = keys | {("a", 2): successor}
    del rotated_keys[("a", 1)]
    control = command | {"id": "0:after", "nonce": 2, "revision": 2, "kind": "mission.resume"}
    before = runtime.state_hash
    with pytest.raises(Failure, match="inactive"):
        runtime.apply(sign_command(control, rotated_keys), height=3)
    assert runtime.state_hash == before
    runtime.apply(sign_command(control, rotated_keys), height=4)
    assert runtime.state.mode == "running"
    assert dict(runtime.state.journal.actors)["a"] == 2
    with pytest.raises(Failure, match="inactive"):
        runtime.apply(raw, height=4)
    with pytest.raises(Failure, match="SIGNATURE"):
        runtime.apply(sign_command(control, keys | rotated_keys), height=4)
    # A new revision cannot start its identity nonce over at one.
    with pytest.raises(Failure, match="NONCE"):
        runtime.apply(sign_command(control | {"id": "0:reset", "nonce": 1}, rotated_keys), height=5)
    next_proposal, _ = schedule(control | {"id": "0:next", "nonce": 3}, revision=3, activation=7)
    runtime.apply(sign_command(next_proposal, rotated_keys), height=5)
    assert decode(state_bytes(runtime.state)) == runtime.state


def test_current_quorum_and_exact_possession_binding():
    runtime, command, keys = runtime_and_command()
    proposal, successor = schedule(command)
    original = runtime.state_hash
    with pytest.raises(Failure, match="QUORUM"):
        runtime.apply(sign_command(proposal, {("a", 1): keys[("a", 1)]}), height=1)
    for field, value in (("nonce", 2), ("id", "0:stolen"), ("chain", "other")):
        changed = proposal | {field: value}
        with pytest.raises(Failure, match="SIGNATURE|CHAIN"):
            runtime.apply(sign_command(changed, keys), height=1)
    for field, value in (("identity", "b"), ("activation_height", 6), ("mission", "other")):
        changed = proposal | {"payload": proposal["payload"] | {field: value}}
        with pytest.raises(Failure, match="possession"):
            runtime.apply(sign_command(changed, keys), height=1)
    for proof in ("0" * 128, "X" * 128, "00"):
        changed = proposal | {"payload": proposal["payload"] | {"proof": proof}}
        with pytest.raises(Failure, match="SIGNATURE"):
            runtime.apply(sign_command(changed, keys), height=1)
    wrong = prove_possession(proposal, keys[("a", 1)])
    with pytest.raises(Failure, match="possession"):
        runtime.apply(sign_command(wrong, keys), height=1)
    assert runtime.state_hash == original
    assert successor.sign(possession_message(proposal)).hex() == proposal["payload"]["proof"]


def test_schedule_rejects_reuse_unknown_slots_pending_and_wrong_revision():
    runtime, command, keys = runtime_and_command()
    for kwargs in (
        {"identity": "unknown"},
        {"revision": 4},
        {"activation": 1},
        {"activation": 10002},
    ):
        proposal, _ = schedule(command, **kwargs)
        with pytest.raises(Failure):
            runtime.apply(sign_command(proposal, keys), height=1)
    proposal, _ = schedule(command)
    reused = prove_possession(
        proposal
        | {
            "payload": proposal["payload"]
            | {"public_key": keys[("b", 1)].public_key().public_bytes_raw().hex()}
        },
        keys[("b", 1)],
    )
    with pytest.raises(Failure, match="key reuse"):
        runtime.apply(sign_command(reused, keys), height=1)
    runtime.apply(sign_command(proposal, keys), height=1)
    pending, _ = schedule(command | {"nonce": 2, "id": "0:pending"}, revision=3)
    with pytest.raises(Failure, match="pending revision"):
        runtime.apply(sign_command(pending, keys), height=2)


def test_compromise_revocation_and_lost_signer_recovery():
    runtime, command, _ = runtime_and_command()
    all_keys, _, _ = setup()
    # Three other organizations recover an identity without the unavailable old key.
    authorities = {key: value for key, value in all_keys.items() if key[0] in "bcd"}
    revoke = command | {
        "actor": "b",
        "kind": "key.revoke",
        "payload": {"mission": "m", "identity": "a", "revision": 1, "reason": "compromise"},
    }
    runtime.apply(sign_command(revoke, authorities), height=1)
    assert next(item for item in runtime.state.credentials if item.identity == "a").revoked
    old = command | {"id": "0:old"}
    with pytest.raises(Failure, match="revoked"):
        runtime.apply(
            sign_command(old, {k: v for k, v in all_keys.items() if k[0] in "abc"}), height=2
        )
    proposal, new_key = schedule(command | {"actor": "b", "nonce": 2, "id": "0:recover"})
    runtime.apply(sign_command(proposal, authorities), height=2)
    active = {k: v for k, v in authorities.items() if k[0] in "bc"} | {("a", 2): new_key}
    control = command | {"id": "0:new", "revision": 2, "nonce": 1}
    runtime.apply(sign_command(control, active), height=4)
    old = next(
        item for item in runtime.state.credentials if item.identity == "a" and item.revision == 1
    )
    assert old.revoked  # A successor never restores the compromised revision's evidence status.
    validator = Draft202012Validator(
        loads(files("checkedflow").joinpath("data/operational-state.schema.json").read_bytes())
    )
    validator.validate(loads(state_bytes(runtime.state)))
    assert decode(state_bytes(runtime.state)) == runtime.state


def test_retired_and_pending_revocation_are_preserved_without_revoking_successor():
    runtime, command, keys = runtime_and_command()
    proposal, _ = schedule(command, identity="worker")
    runtime.apply(sign_command(proposal, keys), height=1)
    for nonce, revision in ((2, 2), (3, 1)):
        revoke = command | {
            "id": f"0:revoke-{revision}",
            "nonce": nonce,
            "kind": "key.revoke",
            "payload": {
                "mission": "m",
                "identity": "worker",
                "revision": revision,
                "reason": "lost",
            },
        }
        runtime.apply(sign_command(revoke, keys), height=nonce)
    assert all(item.revoked for item in runtime.state.credentials if item.identity == "worker")
    assert decode(state_bytes(runtime.state)) == runtime.state


def test_registry_validation_and_proof_context_cannot_be_omitted():
    runtime, command, keys = runtime_and_command()
    proposal, _ = schedule(command)
    _, context = authenticate(
        sign_command(proposal, keys),
        chain=runtime.state.chain,
        epoch=0,
        height=1,
        organizations=frozenset("abcd"),
        registry={(c.identity, c.revision): c for c in runtime.state.credentials},
    )
    with pytest.raises(Failure, match="possession"):
        change(
            runtime.state.credentials,
            "key.schedule",
            proposal["payload"],
            replace(context, possession_key=""),
        )
    initial = runtime.state.credentials
    for altered in (
        tuple(reversed(initial)),
        (initial[0],) + initial,
        (replace(initial[0], revision=2),) + initial[1:],
    ):
        with pytest.raises(Failure):
            validate(altered, 1)
    with pytest.raises(Failure, match="key reuse"):
        validate(tuple(replace(c, public_key=initial[0].public_key) for c in initial), 1)
    runtime.apply(sign_command(proposal, keys), height=1)
    altered = tuple(
        replace(c, role="producer", mission="m") if c.revision == 2 else c
        for c in runtime.state.credentials
    )
    with pytest.raises(Failure, match="lineage"):
        validate(altered, 1)
    for kind, payload in (
        ("key.unknown", {"identity": "a", "revision": 1}),
        ("key.revoke", {"mission": "m", "identity": "a", "revision": 9, "reason": "lost"}),
        ("key.revoke", {"mission": "m", "identity": "a", "revision": 1, "reason": "routine"}),
    ):
        with pytest.raises(Failure):
            change(initial, kind, payload, context)
    with pytest.raises(Failure, match="key schedule"):
        possession_message(command)

    class BadSigner:
        def sign(self, message):
            return b"short"

    with pytest.raises(Failure, match="possession signature"):
        prove_possession(proposal, BadSigner())


def test_governed_rotation_persists_and_signed_history_replays(tmp_path):
    runtime, command, keys = runtime_and_command()
    initial = runtime.state
    store = Store(tmp_path / "control.sqlite", initial)
    proposal, new_key = schedule(command)
    committed = store.commit_block(
        1, [sign_command(proposal, keys)], previous_hash=runtime.state_hash
    )
    assert Store(store.path, initial).load() == store.load()
    for height in (2, 3):
        committed = store.commit_block(height, [], previous_hash=committed.state_hash)
    rotated = {key: value for key, value in keys.items() if key[0] != "a"} | {("a", 2): new_key}
    command.update(revision=2, nonce=2, id="0:after", kind="mission.resume")
    committed = store.commit_block(
        4, [sign_command(command, rotated)], previous_hash=committed.state_hash
    )
    assert committed.outcomes == ("OK",)
    assert store.verify_history(expected_hash=committed.state_hash) == store.load()
    assert Runtime(store.load()).state.mode == "running"


def test_full_revision_history_still_allows_revocation_pause_and_checkpoint():
    runtime, command, _ = runtime_and_command()
    initial = runtime.state
    root = initial.credentials[0]
    count = MAX_REVISIONS - len(initial.credentials) + 1
    lineage = tuple(
        replace(
            root,
            revision=revision,
            activated_height=revision - 1,
            retired_height=revision if revision < count else None,
            public_key=root.public_key
            if revision == 1
            else sha256(f"revision:{revision}".encode()).hexdigest(),
        )
        for revision in range(1, count + 1)
    )
    runtime = Runtime(replace(initial, credentials=lineage + initial.credentials[1:], height=count))
    assert decode(state_bytes(runtime.state)) == runtime.state
    all_keys, _, _ = setup()
    keys = {key: value for key, value in all_keys.items() if key[0] in "bcd"}
    command.update(actor="b")
    proposal, _ = schedule(command, identity="b", activation=count + 2)
    before = runtime.state_hash
    with pytest.raises(Failure, match="CAPACITY"):
        runtime.apply(sign_command(proposal, keys), height=count)
    assert runtime.state_hash == before
    revoke = command | {
        "kind": "key.revoke",
        "payload": {"mission": "m", "identity": "a", "revision": count, "reason": "compromise"},
    }
    runtime.apply(sign_command(revoke, keys), height=count)
    runtime.apply(sign_command(command | {"id": "0:paused", "nonce": 2}, keys), height=count)
    runtime.apply(
        sign_command(
            command | {"id": "0:checkpoint", "nonce": 3, "kind": "journal.rollover"}, keys
        ),
        height=count,
    )
    assert runtime.state.journal.epoch == 1
    assert len(runtime.state.credentials) == MAX_REVISIONS
    with pytest.raises(Failure, match="CAPACITY"):
        validate(runtime.state.credentials + (root,), count)


def test_key_command_schema_and_full_registry_state():
    runtime, command, keys = runtime_and_command()
    validator = Draft202012Validator(
        loads(files("checkedflow").joinpath("data/key-command.schema.json").read_bytes())
    )
    proposal, _ = schedule(command)
    validator.validate(proposal)
    assert not validator.is_valid(proposal | {"kind": "task.submit"})
    assert not validator.is_valid(
        proposal | {"payload": proposal["payload"] | {"unexpected": True}}
    )
    runtime.apply(sign_command(proposal, keys), height=1)
    revoked = command | {
        "id": "0:withdraw",
        "nonce": 2,
        "kind": "key.revoke",
        "payload": {"mission": "m", "identity": "a", "revision": 2, "reason": "lost"},
    }
    validator.validate(revoked)
    assert not validator.is_valid(revoked | {"payload": revoked["payload"] | {"reason": "routine"}})
    for invalid in ([], {}, None, True, 1):
        malformed = revoked | {"payload": revoked["payload"] | {"reason": invalid}}
        with pytest.raises(Failure, match="SHAPE"):
            runtime.apply(sign_command(malformed, keys), height=2)
    runtime.apply(sign_command(revoked, keys), height=2)
    state_validator = Draft202012Validator(
        loads(files("checkedflow").joinpath("data/operational-state.schema.json").read_bytes())
    )
    state_validator.validate(loads(state_bytes(runtime.state)))


def test_lost_pending_revision_can_be_replaced_before_its_activation(tmp_path):
    runtime, command, keys = runtime_and_command()
    initial = runtime.state
    store = Store(tmp_path / "pending.sqlite", initial)
    proposal, lost_key = schedule(command, activation=100)
    result = store.commit_block(1, [sign_command(proposal, keys)], previous_hash=runtime.state_hash)
    revoke = command | {
        "id": "0:lost-pending",
        "nonce": 2,
        "kind": "key.revoke",
        "payload": {"mission": "m", "identity": "a", "revision": 2, "reason": "lost"},
    }
    result = store.commit_block(2, [sign_command(revoke, keys)], previous_hash=result.state_hash)
    replacement, new_key = schedule(
        command | {"id": "0:replace-pending", "nonce": 3}, revision=3, activation=5
    )
    result = store.commit_block(
        3, [sign_command(replacement, keys)], previous_hash=result.state_hash
    )
    assert result.outcomes == ("OK",)
    assert Store(store.path, initial).load() == store.load()
    lineage = [item for item in store.load().credentials if item.identity == "a"]
    assert lineage[0].retired_height == 5
    assert lineage[1].revoked and lineage[1].activated_height == 100
    assert lineage[2].activated_height == 5
    # The original key still works before replacement, then loses authority exactly once.
    control = command | {"id": "0:before", "nonce": 4, "kind": "mission.resume"}
    result = store.commit_block(4, [sign_command(control, keys)], previous_hash=result.state_hash)
    successor_keys = {k: v for k, v in keys.items() if k[0] != "a"} | {("a", 3): new_key}
    after = control | {"id": "0:after", "nonce": 5, "revision": 3}
    result = store.commit_block(
        5,
        [sign_command(after, successor_keys), sign_command(control, keys)],
        previous_hash=result.state_hash,
    )
    assert result.outcomes == ("OK", "SIGNATURE")
    restored = Store(store.path, initial)
    assert restored.verify_history(expected_hash=result.state_hash) == restored.load()
    runtime = Runtime(restored.load())
    lost_signers = {k: v for k, v in keys.items() if k[0] != "a"} | {("a", 2): lost_key}
    with pytest.raises(Failure, match="revoked"):
        runtime.apply(sign_command(after | {"revision": 2}, lost_signers), height=100)


@given(st.lists(st.integers(min_value=1, max_value=200), min_size=1, max_size=6))
@settings(max_examples=25, deadline=None)
def test_replacement_never_extends_a_previous_authority_interval(delays):
    runtime, command, _ = runtime_and_command()
    all_keys, _, _ = setup()
    keys = {key: value for key, value in all_keys.items() if key[0] in "bcd"}
    command.update(actor="b")
    nonce = 0
    for revision, delay in enumerate(delays, 2):
        height = (revision - 2) * 3 + 1
        nonce += 1
        proposal, _ = schedule(
            command | {"id": f"0:schedule-{revision}", "nonce": nonce},
            revision=revision,
            activation=height + delay,
        )
        before = {item.revision: item for item in runtime.state.credentials if item.identity == "a"}
        runtime.apply(sign_command(proposal, keys), height=height)
        for item in runtime.state.credentials:
            if item.identity == "a" and item.revision in before:
                old = before[item.revision]
                assert item.revoked == old.revoked
                if old.retired_height is not None:
                    assert item.retired_height <= old.retired_height
                # Independent finite probe across every possible activation in this test.
                for probe in range(225):
                    assert not item.usable_at(probe) or old.usable_at(probe)
        nonce += 1
        revoke = command | {
            "id": f"0:revoke-{revision}",
            "nonce": nonce,
            "kind": "key.revoke",
            "payload": {"mission": "m", "identity": "a", "revision": revision, "reason": "lost"},
        }
        runtime.apply(sign_command(revoke, keys), height=height + 1)
        assert decode(state_bytes(runtime.state)) == runtime.state


def test_rotation_cannot_resurrect_an_expired_key_or_overlap_live_intervals():
    runtime, command, _ = runtime_and_command()
    state = runtime.state
    credentials = tuple(
        replace(item, retired_height=2) if item.identity == "a" else item
        for item in state.credentials
    )
    runtime = Runtime(replace(state, credentials=credentials, height=3))
    all_keys, _, _ = setup()
    keys = {key: value for key, value in all_keys.items() if key[0] in "bcd"}
    proposal, _ = schedule(command | {"actor": "b"}, activation=5)
    runtime.apply(sign_command(proposal, keys), height=3)
    old = runtime.state.credentials[0]
    assert old.retired_height == 2 and not old.usable_at(3)
    assert decode(state_bytes(runtime.state)) == runtime.state
    overlapping = (replace(old, retired_height=6), *runtime.state.credentials[1:])
    with pytest.raises(Failure, match="overlapping"):
        validate(overlapping, 3)
