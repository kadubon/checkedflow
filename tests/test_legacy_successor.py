"""Real signed successor work cannot reset historical funding or replay old authority."""

from dataclasses import replace
from importlib.resources import files
from io import BytesIO

import pytest
from jsonschema import Draft202012Validator
from test_legacy_capture import capture
from test_operational_runtime import runtime_and_command
from test_work_budget import reserve, settle

from checkedflow.core.values import Failure
from checkedflow.core.work_budget import change, validate
from checkedflow.legacy_inventory import Checkpoint
from checkedflow.legacy_successor import prepare
from checkedflow.operational_backup import export_history, restore_history
from checkedflow.operational_codec import decode, state_bytes
from checkedflow.operational_identity import sign_command
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import Store
from checkedflow.recovery import replay_blocks
from checkedflow.serialization import decode as decode_legacy
from checkedflow.serialization import encode
from checkedflow.wire import digest, document, dumps


def test_portable_successor_vector_and_schema():
    data = files("checkedflow").joinpath("data")
    vector = document(data.joinpath("legacy-successor-vector.json").read_bytes())
    legacy = document(data.joinpath("legacy-v1.json").read_bytes())
    checkpoint = Checkpoint(**vector["checkpoint"])
    state = prepare(
        dumps(legacy["final_state"]),
        checkpoint,
        decode(dumps(vector["initial"])),
        mission=vector["mission"],
    )
    value = document(state_bytes(state))
    assert value == vector["successor"]
    assert digest(value) == vector["successor_hash"]
    assert {
        "spent": state.budget.spent,
        "reserved": state.budget.reserved,
        "available": state.budget.available,
    } == vector["balances"]
    Draft202012Validator(
        document(data.joinpath("operational-state.schema.json").read_bytes())
    ).validate(value)


def source(*, funded=False):
    fixture = capture()
    if not funded:
        value = fixture["final_state"]
    else:
        state = decode_legacy(fixture["initial"])
        for block in fixture["blocks"]:
            state = replay_blocks(state, [block]).state
            if state.missions and state.missions["m"].reserved:
                break
        value = encode(state)
    return dumps(value), Checkpoint(value["chain"], value["height"], digest(value))


@pytest.mark.parametrize("funded", [False, True])
def test_real_legacy_funding_survives_signed_work_and_persistent_replay(tmp_path, funded):
    old, anchor = source(funded=funded)
    original = document(old)["missions"]["m"]
    fresh, command, keys = runtime_and_command()
    initial = prepare(old, anchor, fresh.state, mission="m")
    assert initial.mode == "paused" and not initial.tasks and not initial.candidates
    assert initial.budget.inheritance.state_hash == anchor.state_hash
    assert initial.budget.spent == original["spent"]
    assert initial.budget.reserved == original["reserved"]
    assert decode(state_bytes(initial)) == initial
    runtime = Runtime(initial)
    store = Store(tmp_path / "successor.sqlite", initial)
    actions = [
        ("mission.resume", {}),
        ("budget.reserve", {"phase": "verify", "ceiling": 1, "target": "a" * 64}),
        ("budget.settle", {"ticket": "0:2", "outcome": "unknown", "charged": 1}),
        ("mission.pause", {}),
        ("journal.rollover", {}),
    ]
    for nonce, (kind, payload) in enumerate(actions, 1):
        raw = sign_command(
            command
            | {
                "id": f"0:{nonce}",
                "nonce": nonce,
                "kind": kind,
                "payload": {"mission": "m"} | payload,
            },
            keys,
        )
        previous = runtime.state_hash
        runtime.apply(raw, height=nonce)
        assert (
            store.commit_block(nonce, [raw], previous_hash=previous).state_hash
            == runtime.state_hash
        )
    assert store.load() == runtime.state
    store.verify_history(expected_hash=runtime.state_hash)
    assert runtime.state.budget.spent == original["spent"] + 1
    assert runtime.state.budget.reserved == original["reserved"]
    assert runtime.state.budget.inheritance == initial.budget.inheritance
    output = BytesIO()
    checkpoint = export_history(store, output, expected_hash=runtime.state_hash)
    restored = restore_history(
        BytesIO(output.getvalue()),
        tmp_path / "restored",
        initial=initial,
        checkpoint=checkpoint,
        current_height=5,
    )
    assert restored.load() == runtime.state
    with pytest.raises(Failure):
        runtime.apply(bytes.fromhex(capture()["blocks"][0]["transactions"][0]["raw"]), height=6)


def test_reservation_cannot_be_released_as_an_ordinary_ticket_or_spent_twice():
    raw, checkpoint = source(funded=True)
    fresh, _, _ = runtime_and_command()
    inherited = prepare(raw, checkpoint, fresh.state, mission="m").budget
    assert inherited.reserved > 0
    with pytest.raises(Failure, match="NOT_FOUND"):
        settle(inherited, "released", 0, identity="old-attempt")
    with pytest.raises(Failure, match="BUDGET"):
        reserve(inherited, phase="verify", ceiling=inherited.available + 1)
    charged = settle(reserve(inherited, ceiling=1, phase="verify"), "unknown", 1)
    assert charged.spent == inherited.spent + 1
    assert charged.reserved == inherited.reserved
    assert charged.available == inherited.available - 1
    with pytest.raises(Failure, match="STATE"):
        change(
            inherited,
            "budget.configure",
            {"mission": "m", "budget": 10000, "verification_reserve": 1},
            request="0:reset",
            running=False,
        )


def test_successor_store_rejects_preloaded_new_work(tmp_path):
    raw, checkpoint = source()
    fresh, _, _ = runtime_and_command()
    initial = prepare(raw, checkpoint, fresh.state, mission="m")
    preloaded = replace(initial, budget=reserve(initial.budget, ceiling=1, phase="verify"))
    with pytest.raises(Failure, match="GENESIS"):
        Store(tmp_path / "invalid.sqlite", preloaded)
    assert not (tmp_path / "invalid.sqlite").exists()


@pytest.mark.parametrize("damage", ["same_chain", "missing_mission", "running", "budget"])
def test_preparation_rejects_ambiguous_successors(damage):
    raw, checkpoint = source()
    fresh, _, _ = runtime_and_command()
    state, mission = fresh.state, "m"
    if damage == "same_chain":
        state = replace(state, chain=checkpoint.chain)
    elif damage == "missing_mission":
        mission = "missing"
    elif damage == "running":
        state = replace(state, mode="running")
    else:
        state = replace(state, budget=replace(state.budget, budget=10, verification_reserve=1))
    with pytest.raises(Failure, match="CHAIN|NOT_FOUND|STATE"):
        prepare(raw, checkpoint, state, mission=mission)


def test_exhausted_legacy_budget_does_not_require_a_fabricated_verification_charge():
    raw, checkpoint = source()
    value = document(raw)
    value["missions"]["m"]["spent"] = value["missions"]["m"]["budget"]
    value["missions"]["m"]["reserved"] = 0
    for task in value["tasks"].values():
        task["funded"] = False
    fresh, _, _ = runtime_and_command()
    # Explicit synthetic trusted accounting boundary; not an authentic historic fixture.
    state = prepare(
        dumps(value), replace(checkpoint, state_hash=digest(value)), fresh.state, mission="m"
    )
    assert state.budget.available == state.budget.protected_verification == 0
    assert state.budget.archived_verification == 0
    with pytest.raises(Failure, match="BUDGET"):
        reserve(state.budget, ceiling=1, phase="verify")


@pytest.mark.parametrize("damage", ["allowance", "hash", "reserved", "same_chain", "null"])
def test_inherited_wire_state_rejects_inconsistent_commitments(damage):
    raw, checkpoint = source()
    fresh, _, _ = runtime_and_command()
    state = prepare(raw, checkpoint, fresh.state, mission="m")
    value = document(state_bytes(state))
    inheritance = value["budget"]["inheritance"]
    if damage == "allowance":
        value["budget"]["budget"] += 1
    elif damage == "hash":
        inheritance["state_hash"] = "invalid"
    elif damage == "reserved":
        inheritance["reserved"] = inheritance["budget"]
    elif damage == "same_chain":
        inheritance["chain"] = state.chain
    else:
        value["budget"]["inheritance"] = None
    with pytest.raises(Failure):
        decode(dumps(value))
    validate(state.budget)
