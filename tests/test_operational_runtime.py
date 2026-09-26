"""Authenticated control transitions and replay isolation for the unreleased v2 profile."""

from dataclasses import replace

import pytest
from test_operational_identity import setup

from checkedflow.core.operational import genesis, transition
from checkedflow.core.request_journal import Limits
from checkedflow.core.values import Failure
from checkedflow.operational_identity import authenticate, sign_command
from checkedflow.operational_runtime import Runtime


def runtime_and_command(**limit_changes):
    keys, registry, command = setup()
    state = genesis(
        "operational-test",
        "m",
        tuple("abcd"),
        tuple(registry.values()),
        limits=Limits(**limit_changes),
    )
    runtime = Runtime(state)
    command.update(epoch=0, id="0:pause")
    keys = {key: value for key, value in keys.items() if key[0] in "abc"}
    return runtime, command, keys


def test_controls_require_current_quorum_and_signed_scope():
    runtime, command, keys = runtime_and_command()
    original = runtime.state_hash
    with pytest.raises(Failure, match="three administrative organizations"):
        runtime.apply(sign_command(command, {("a", 1): keys[("a", 1)]}), height=1)
    assert runtime.state_hash == original
    with pytest.raises(Failure, match="mission mismatch"):
        runtime.apply(sign_command(command | {"payload": {"mission": "other"}}, keys), height=1)
    assert runtime.state_hash == original
    with pytest.raises(Failure, match="not supported"):
        runtime.apply(sign_command(command | {"kind": "task.start"}, keys), height=1)
    assert runtime.state_hash == original


def test_replaying_an_old_pause_does_not_pause_newly_resumed_mission():
    runtime, command, keys = runtime_and_command()
    pause = sign_command(command, keys)
    assert runtime.apply(pause, height=1) is None
    resume = command | {"id": "0:resume", "nonce": 2, "kind": "mission.resume"}
    runtime.apply(sign_command(resume, keys), height=2)
    assert runtime.state.mode == "running"
    runtime.apply(pause, height=3)
    assert runtime.state.mode == "running"
    assert dict(runtime.state.journal.actors)["a"] == 2
    drain = command | {"id": "0:drain", "nonce": 3, "kind": "mission.drain"}
    runtime.apply(sign_command(drain, keys), height=4)
    assert runtime.state.mode == "draining"


def test_saturated_controls_can_checkpoint_without_reopening_old_ids():
    runtime, command, keys = runtime_and_command(administrative_count=1)
    pause = sign_command(command, keys)
    runtime.apply(pause, height=1)
    rollover = command | {"id": "0:checkpoint", "nonce": 2, "kind": "journal.rollover"}
    archive = runtime.apply(sign_command(rollover, keys), height=2)
    assert archive is not None
    assert runtime.state.journal.archive_root == archive.root
    assert runtime.state.journal.receipts == ()
    assert runtime.state.journal.epoch == 1
    before = runtime.state_hash
    with pytest.raises(Failure, match="request epoch retired"):
        runtime.apply(pause, height=3)
    assert runtime.state_hash == before
    renamed_epoch = command | {"epoch": 1, "nonce": 3}
    with pytest.raises(Failure, match="request identity retired"):
        runtime.apply(sign_command(renamed_epoch, keys), height=3)
    resume = command | {"epoch": 1, "nonce": 3, "id": "1:resume", "kind": "mission.resume"}
    runtime.apply(sign_command(resume, keys), height=3)
    assert runtime.state.mode == "running"


def test_explicit_block_height_survives_a_rejected_command():
    runtime, command, keys = runtime_and_command()
    with pytest.raises(Failure, match="unsupported state profile"):
        Runtime(replace(runtime.state, profile="checkedflow/state/v1"))
    runtime.tick(10)
    expected = runtime.state_hash
    with pytest.raises(Failure, match="height cannot decrease"):
        runtime.apply(sign_command(command, keys), height=9)
    assert runtime.state_hash == expected
    with pytest.raises(Failure):
        runtime.apply(b"{}", height=10)
    assert runtime.state.height == 10
    with pytest.raises(Failure):
        runtime.tick(9)


def test_replay_matches_state_and_archive_commitments():
    left, command, keys = runtime_and_command()
    right = Runtime(left.state)
    for index, kind in enumerate(["mission.resume", "mission.pause", "journal.rollover"], 1):
        raw = sign_command(command | {"id": f"0:{index}", "nonce": index, "kind": kind}, keys)
        assert left.apply(raw, height=index) == right.apply(raw, height=index)
        assert left.state_hash == right.state_hash


def test_genesis_rejects_shared_keys_and_missing_organization_authority():
    _, registry, _ = setup()
    credentials = tuple(registry.values())
    with pytest.raises(Failure, match="four organizations"):
        genesis("chain", "m", tuple("abc"), credentials)
    changed = (replace(credentials[0], public_key=credentials[1].public_key), *credentials[1:])
    with pytest.raises(Failure, match="distinct initial"):
        genesis("chain", "m", tuple("abcd"), changed)
    changed = (replace(credentials[0], organization="b"), *credentials[1:])
    with pytest.raises(Failure, match="one initial administrator"):
        genesis("chain", "m", tuple("abcd"), changed)
    changed = (*credentials[:-1], replace(credentials[-1], mission="other"))
    with pytest.raises(Failure, match="scope or revision"):
        genesis("chain", "m", tuple("abcd"), changed)


def test_authenticated_context_cannot_be_reused_after_registry_withdrawal():
    runtime, command, keys = runtime_and_command()
    state = runtime.state
    parsed, context = authenticate(
        sign_command(command, keys),
        chain=state.chain,
        epoch=0,
        height=1,
        organizations=frozenset(state.organizations),
        registry={(item.identity, item.revision): item for item in state.credentials},
    )
    withdrawn = replace(
        state,
        credentials=tuple(
            replace(item, revoked=True) if item.identity == "a" else item
            for item in state.credentials
        ),
    )
    with pytest.raises(Failure, match="current registry"):
        transition(withdrawn, parsed, context)
