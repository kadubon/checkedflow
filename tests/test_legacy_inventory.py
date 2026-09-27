"""Migration prerequisites must preserve real old obligations, not mint new funds."""

from dataclasses import replace

import pytest
from test_legacy_capture import FINAL_HASH, capture

from checkedflow.core.values import Failure
from checkedflow.legacy_inventory import Checkpoint, inspect_snapshot
from checkedflow.recovery import replay_blocks
from checkedflow.serialization import decode, encode
from checkedflow.wire import digest, document, dumps


def snapshot():
    fixture = capture()
    state = replay_blocks(decode(fixture["initial"]), fixture["blocks"]).state
    return dumps(encode(state)), Checkpoint(state.chain, state.height, FINAL_HASH)


def test_authentic_history_preserves_all_fields_and_charged_unknowns():
    raw, anchor = snapshot()
    inventory = inspect_snapshot(raw, anchor)
    original = document(raw)
    assert document(inventory.snapshot) == original
    assert inventory.checkpoint == anchor
    assert "unknown" in inventory.pending_tasks
    assert {"c", "child"} <= set(inventory.withdrawn_capabilities)
    assert inventory.open_residuals
    mission = original["missions"]["m"]
    balance = inventory.balances[0]
    assert (balance.budget, balance.spent, balance.reserved) == (
        mission["budget"],
        mission["spent"],
        mission["reserved"],
    )
    assert balance.available + balance.spent + balance.reserved == balance.budget
    assert original["tasks"]["unknown"]["funded"] is False
    # Returned bytes retain fences, nonces, retired keys, dependencies and resolutions.
    changed = document(inventory.snapshot)
    changed["nonces"].clear()
    assert document(inventory.snapshot) == original


def test_every_real_checkpoint_including_funded_work_is_lossless():
    fixture = capture()
    state = decode(fixture["initial"])
    saw_reservation = False
    for block in fixture["blocks"]:
        runtime = replay_blocks(state, [block])
        state = runtime.state
        raw = dumps(encode(state))
        inventory = inspect_snapshot(raw, Checkpoint(state.chain, state.height, runtime.state_hash))
        assert inventory.snapshot == raw
        saw_reservation |= any(balance.reserved > 0 for balance in inventory.balances)
    assert saw_reservation


@pytest.mark.parametrize(
    "field,value",
    [("chain", "foreign"), ("height", 0), ("state_hash", "0" * 64), ("state_hash", "invalid")],
)
def test_checkpoint_is_independent_of_snapshot(field, value):
    raw, anchor = snapshot()
    with pytest.raises(Failure, match="CHECKPOINT"):
        inspect_snapshot(raw, replace(anchor, **{field: value}))


@pytest.mark.parametrize("damage", ["spent", "reservations", "mission", "extra"])
def test_even_operator_anchored_inconsistent_snapshot_is_rejected(damage):
    raw, anchor = snapshot()
    value = document(raw)
    if damage == "spent":
        value["missions"]["m"]["spent"] = value["missions"]["m"]["budget"] + 1
    elif damage == "reservations":
        value["missions"]["m"]["reserved"] += 1
    elif damage == "mission":
        value["tasks"]["unknown"]["mission"] = "missing"
    else:
        value["unexpected"] = True
    with pytest.raises(Failure, match="INVARIANT|SHAPE"):
        inspect_snapshot(dumps(value), replace(anchor, state_hash=digest(value)))


def test_lexical_and_byte_bounds_precede_checkpoint_validation():
    raw, anchor = snapshot()
    with pytest.raises(Failure, match="LIMIT"):
        inspect_snapshot(b" " * 4_194_305, anchor)
    with pytest.raises(Failure, match="DUPLICATE_KEY"):
        inspect_snapshot(b'{"chain":"x","chain":"x"}', anchor)
    with pytest.raises(Failure, match="CHECKPOINT"):
        inspect_snapshot(raw.replace(b'"spent":', b'"spend":', 1), anchor)
