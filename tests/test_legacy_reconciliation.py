"""Governed inherited settlement preserves original charges and never creates work."""

from dataclasses import replace

import pytest
from test_legacy_successor import source
from test_operational_runtime import runtime_and_command

from checkedflow.core.values import Failure
from checkedflow.core.work_budget import change, validate
from checkedflow.legacy_successor import prepare
from checkedflow.operational_codec import decode, state_bytes
from checkedflow.operational_identity import sign_command
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import Store
from checkedflow.wire import document, dumps


def setup(funded):
    old, checkpoint = source(funded=funded)
    runtime, command, keys = runtime_and_command()
    initial = prepare(old, checkpoint, runtime.state, mission="m")
    obligation = next(
        item for item in initial.budget.inheritance.obligations if item.funded == funded
    )
    payload = {
        "mission": "m",
        "checkpoint": checkpoint.state_hash,
        "task": obligation.identity,
        "outcome": "unknown",
        "evidence": "a" * 64,
    }
    return initial, command, keys, payload, obligation


@pytest.mark.parametrize("funded", [False, True])
@pytest.mark.parametrize("outcome", ["executed", "not_executed"])
def test_unknown_reconciliation_never_refunds_or_double_charges(tmp_path, funded, outcome):
    initial, command, keys, payload, obligation = setup(funded)
    runtime = Runtime(initial)
    store = Store(tmp_path / "state.db", initial)
    for nonce, result in enumerate(("unknown", outcome), 1):
        raw = sign_command(
            command
            | {
                "id": f"0:{nonce}",
                "nonce": nonce,
                "kind": "budget.reconcile_inherited",
                "payload": payload | {"outcome": result},
            },
            keys,
        )
        prior = runtime.state_hash
        runtime.apply(raw, height=nonce)
        assert (
            store.commit_block(nonce, [raw], previous_hash=prior).state_hash == runtime.state_hash
        )
    budget = runtime.state.budget
    additional = obligation.ceiling if funded else 0
    assert budget.spent == initial.budget.spent + additional
    assert budget.reserved == initial.budget.reserved - additional
    assert budget.available == initial.budget.available
    assert not runtime.state.tasks and not runtime.state.effects
    assert decode(state_bytes(runtime.state)) == runtime.state
    assert store.verify_history(expected_hash=runtime.state_hash) == runtime.state
    assert budget.inheritance.spent == initial.budget.inheritance.spent
    with pytest.raises(Failure, match="STATE"):
        change(budget, "budget.reconcile_inherited", payload, request="other", running=False)


def test_confirmed_nonexecution_releases_only_original_held_reservation():
    initial, _, _, payload, obligation = setup(True)
    result = change(
        initial.budget,
        "budget.reconcile_inherited",
        payload | {"outcome": "not_executed"},
        request="0:1",
        running=False,
    )
    assert result.spent == initial.budget.spent
    assert result.available == initial.budget.available + obligation.ceiling
    assert result.inheritance.reserved == initial.budget.inheritance.reserved


def test_reconciliation_requires_authority_checkpoint_and_evidence():
    initial, command, keys, payload, _ = setup(True)
    runtime = Runtime(initial)
    raw = sign_command(
        command | {"kind": "budget.reconcile_inherited", "payload": payload},
        dict(list(keys.items())[:2]),
    )
    with pytest.raises(Failure):
        runtime.apply(raw, height=1)
    assert runtime.state == initial
    for patch in (
        {"checkpoint": "0" * 64},
        {"task": "missing"},
        {"outcome": "refund"},
        {"evidence": ""},
        {"evidence": "x" * 64},
    ):
        with pytest.raises(Failure):
            change(
                initial.budget,
                "budget.reconcile_inherited",
                payload | patch,
                request="0:1",
                running=False,
            )
    with pytest.raises(Failure, match="BINDING"):
        change(
            replace(initial.budget, inheritance=None),
            "budget.reconcile_inherited",
            payload,
            request="0:1",
            running=False,
        )


def test_liability_codec_and_invariants_reject_forged_accounting():
    initial, _, _, _, item = setup(True)
    for replacement in (
        replace(item, charged=1),
        replace(item, funded=1),
        replace(item, task_hash="x" * 64),
        replace(item, ceiling=0),
        replace(item, outcome="unknown", evidence="a" * 64, charged=0),
    ):
        with pytest.raises(Failure):
            validate(
                replace(
                    initial.budget,
                    inheritance=replace(initial.budget.inheritance, obligations=(replacement,)),
                )
            )
    value = document(state_bytes(initial))
    for field in ("outcome", "evidence", "funded"):
        changed = document(dumps(value))
        changed["budget"]["inheritance"]["obligations"][0][field] = 0
        with pytest.raises(Failure):
            decode(dumps(changed))


def test_earlier_preparation_bytes_preserve_held_funding_without_resolution_inventory():
    initial, _, _, payload, _ = setup(True)
    budget = replace(
        initial.budget, inheritance=replace(initial.budget.inheritance, obligations=())
    )
    state = replace(initial, budget=budget)
    encoded = document(state_bytes(state))
    assert "obligations" not in encoded["budget"]["inheritance"]
    assert decode(dumps(encoded)) == state
    assert budget.reserved == initial.budget.reserved and budget.spent == initial.budget.spent
    with pytest.raises(Failure, match="NOT_FOUND"):
        change(budget, "budget.reconcile_inherited", payload, request="0:1", running=False)


def test_reconciled_state_cannot_be_reset_as_new_store_genesis(tmp_path):
    initial, _, _, payload, _ = setup(True)
    budget = change(
        initial.budget, "budget.reconcile_inherited", payload, request="0:1", running=False
    )
    path = tmp_path / "forged.db"
    with pytest.raises(Failure):
        Store(path, replace(initial, budget=budget))
    assert not path.exists()
