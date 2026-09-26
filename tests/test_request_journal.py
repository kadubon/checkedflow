"""Bounded epoch receipts, permanent retirement and emergency checkpoint capacity."""

from dataclasses import replace
from hashlib import sha256

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from checkedflow.core.request_journal import Archive, Limits, Receipt, admit, genesis, rollover
from checkedflow.core.values import Failure


def receipt(journal, name, *, actor="worker", administrative=False):
    return Receipt(
        f"{journal.epoch}:{name}",
        actor,
        dict(journal.actors)[actor] + 1,
        sha256(name.encode()).hexdigest(),
        administrative,
    )


def test_duplicate_is_acknowledged_without_advancing_nonce_or_consuming_capacity():
    initial = genesis(("worker", "admin"), Limits(ordinary_count=1))
    command = receipt(initial, "one")
    state, duplicate = admit(initial, command)
    assert not duplicate and dict(initial.actors)["worker"] == 0
    repeated, duplicate = admit(state, command)
    assert repeated is state and duplicate
    for changed in [replace(command, command_digest="a" * 64), replace(command, actor="admin")]:
        with pytest.raises(Failure, match="different command"):
            admit(state, changed)
    with pytest.raises(Failure, match="count exhausted"):
        admit(state, receipt(state, "two"))
    assert dict(state.actors)["worker"] == 1


def test_ordinary_saturation_preserves_control_and_checkpoint_capacity():
    state = genesis(("worker", "admin"), Limits(ordinary_count=1, administrative_count=1))
    state, _ = admit(state, receipt(state, "ordinary"))
    state, _ = admit(state, receipt(state, "pause", actor="admin", administrative=True))
    with pytest.raises(Failure, match="count exhausted"):
        admit(state, receipt(state, "another-pause", actor="admin", administrative=True))
    checkpoint = receipt(state, "checkpoint", actor="admin", administrative=True)
    successor, archived = rollover(state, checkpoint)
    assert successor.receipts == () and successor.epoch == 1
    assert len(archived.receipts) == 3
    assert successor.archive_root == archived.root
    assert dict(successor.actors) == {"admin": 2, "worker": 1}
    with pytest.raises(Failure, match="identity retired"):
        admit(successor, checkpoint)
    with pytest.raises(Failure, match="identity retired"):
        admit(successor, replace(checkpoint, nonce=3))


def test_byte_exhaustion_does_not_borrow_administrative_reserve():
    state = genesis(("worker", "admin"), Limits(ordinary_bytes=64))
    with pytest.raises(Failure, match="byte ceiling"):
        admit(state, receipt(state, "x" * 70))
    control = receipt(state, "x" * 70, actor="admin", administrative=True)
    changed, _ = admit(state, control)
    assert changed.receipts == (control,)


@pytest.mark.parametrize("request_id", ["bad", "0:", "00:name", "-1:name", "１:name", "1:name"])
def test_noncanonical_or_future_epoch_names_are_rejected(request_id):
    state = genesis(("worker",))
    with pytest.raises(Failure):
        admit(state, replace(receipt(state, "one"), request=request_id))


def test_nonce_actor_and_checkpoint_admission():
    state = genesis(("worker", "admin"))
    command = receipt(state, "one")
    with pytest.raises(Failure, match="nonce"):
        admit(state, replace(command, nonce=2))
    with pytest.raises(Failure, match="unregistered"):
        admit(state, replace(command, actor="unknown"))
    with pytest.raises(Failure, match="administrative"):
        rollover(state, command)
    admin = receipt(state, "pause", actor="admin", administrative=True)
    state, _ = admit(state, admin)
    with pytest.raises(Failure, match="fresh request"):
        rollover(state, admin)


def test_archive_commitment_binds_order_and_predecessor():
    state = genesis(("worker", "admin"))
    state, _ = admit(state, receipt(state, "one"))
    successor, first = rollover(
        state, receipt(state, "checkpoint", actor="admin", administrative=True)
    )
    _, second = rollover(
        successor, receipt(successor, "checkpoint", actor="admin", administrative=True)
    )
    assert second.previous_root == first.root
    assert replace(first, receipts=tuple(reversed(first.receipts))).root != first.root
    assert replace(first, previous_root="a" * 64).root != first.root
    assert replace(first, epoch=1).root != first.root
    assert isinstance(first, Archive)


def test_invalid_identity_unicode_and_missing_reserve_are_rejected():
    with pytest.raises(Failure, match="Unicode"):
        genesis(("\ud800",))
    with pytest.raises(Failure):
        Limits(administrative_count=2, administrative_bytes=209)


@settings(max_examples=30, deadline=None)
@given(st.lists(st.booleans(), min_size=1, max_size=80))
def test_reference_nonce_model_survives_repeated_rollovers(rollovers):
    state = genesis(("worker", "admin"), Limits(ordinary_count=128))
    model = {"worker": 0, "admin": 0}
    epoch = 0
    for index, checkpoint in enumerate(rollovers):
        if checkpoint:
            state, _ = rollover(
                state, receipt(state, str(index), actor="admin", administrative=True)
            )
            model["admin"] += 1
            epoch += 1
        else:
            state, _ = admit(state, receipt(state, str(index)))
            model["worker"] += 1
        assert dict(state.actors) == model
        assert state.epoch == epoch


def test_five_thousand_requests_keep_active_state_bounded():
    # Fixed CPU-only dimensions: 5,000 worker admissions, 64 receipts per epoch, two actors.
    state = genesis(("worker", "admin"), Limits(ordinary_count=64))
    first = receipt(state, "0")
    for index in range(5000):
        if len(state.receipts) == 64:
            state, _ = rollover(
                state, receipt(state, "checkpoint", actor="admin", administrative=True)
            )
        state, _ = admit(state, receipt(state, str(index)))
        assert len(state.receipts) <= 64 and len(state.actors) == 2
    assert dict(state.actors)["worker"] == 5000
    assert state.epoch == 78
    with pytest.raises(Failure, match="identity retired"):
        admit(state, first)
