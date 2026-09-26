"""Real signed funding transitions and independent accounting reference model."""

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_operational_identity import setup
from test_operational_runtime import runtime_and_command

from checkedflow.core.values import Failure
from checkedflow.core.work_budget import Ledger, Ticket, change, validate
from checkedflow.operational_codec import decode, state_bytes
from checkedflow.operational_identity import sign_command
from checkedflow.operational_storage import Store


def reserve(ledger, request="0:ticket", ceiling=20, phase="execute", running=True):
    return change(
        ledger,
        "budget.reserve",
        {"mission": "m", "phase": phase, "ceiling": ceiling, "target": "a" * 64},
        request=request,
        running=running,
    )


def settle(ledger, outcome, charged, identity="0:ticket"):
    return change(
        ledger,
        "budget.settle",
        {"mission": "m", "ticket": identity, "outcome": outcome, "charged": charged},
        request="0:finish",
        running=False,
    )


def test_signed_reservations_survive_rollover_and_unknown_is_conservative(tmp_path):
    runtime, template, keys = runtime_and_command()
    store = Store(tmp_path / "budget.sqlite", runtime.state)
    commands = [
        ("budget.configure", {"budget": 100, "verification_reserve": 20}),
        ("mission.resume", {}),
        ("budget.reserve", {"phase": "execute", "ceiling": 60, "target": "a" * 64}),
        ("mission.pause", {}),
        ("budget.settle", {"ticket": "0:3", "outcome": "unknown", "charged": 60}),
        ("journal.rollover", {}),
    ]
    for nonce, (kind, payload) in enumerate(commands, 1):
        command = template | {
            "id": f"0:{nonce}",
            "nonce": nonce,
            "kind": kind,
            "payload": {"mission": "m"} | payload,
        }
        raw = sign_command(command, keys)
        before = runtime.state_hash
        runtime.apply(raw, height=nonce)
        result = store.commit_block(nonce, [raw], previous_hash=before)
        assert result.state_hash == runtime.state_hash
        assert decode(state_bytes(runtime.state)) == runtime.state
    assert runtime.state.budget.spent == 60
    assert runtime.state.budget.available == 40 and runtime.state.budget.reserved == 0
    assert runtime.state.budget.tickets[0].status == "unknown"
    assert store.load() == runtime.state
    store.verify_history(expected_hash=runtime.state_hash)


def test_budget_transitions_preserve_control_capacity_and_idempotency():
    runtime, command, keys = runtime_and_command(ordinary_count=1)

    def send(nonce, kind, payload):
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
        runtime.apply(raw, height=nonce)
        return raw

    send(1, "budget.configure", {"budget": 100, "verification_reserve": 20})
    send(2, "mission.resume", {})
    raw = send(3, "budget.reserve", {"phase": "verify", "ceiling": 20, "target": "a" * 64})
    runtime.apply(raw, height=3)
    assert runtime.state.budget.reserved == 20
    before = runtime.state_hash
    with pytest.raises(Failure, match="CAPACITY"):
        send(4, "budget.reserve", {"phase": "repair", "ceiling": 10, "target": "a" * 64})
    assert runtime.state_hash == before
    send(4, "mission.drain", {})
    send(5, "budget.settle", {"ticket": "0:3", "outcome": "settled", "charged": 12})
    assert runtime.state.budget.available == 88


def test_rejects_budget_excess_reconfiguration_and_terminal_rewriting():
    ledger = change(
        Ledger(),
        "budget.configure",
        {"mission": "m", "budget": 100, "verification_reserve": 20},
        request="0:init",
        running=False,
    )
    with pytest.raises(Failure, match="already configured"):
        change(
            ledger,
            "budget.configure",
            {"mission": "m", "budget": 200, "verification_reserve": 20},
            request="0:again",
            running=False,
        )
    for kwargs in ({"running": False}, {"ceiling": 101}, {"phase": "invented"}):
        with pytest.raises(Failure):
            reserve(ledger, **kwargs)
    reserved = reserve(ledger)
    with pytest.raises(Failure, match="DUPLICATE"):
        reserve(reserved)
    for outcome, charged, identity in [
        ("unknown", 19, "0:ticket"),
        ("released", 1, "0:ticket"),
        ("wrong", 0, "0:ticket"),
        ("settled", 21, "0:ticket"),
        ("settled", 0, "missing"),
    ]:
        with pytest.raises(Failure):
            settle(reserved, outcome, charged, identity)
    unknown = settle(reserved, "unknown", 20)
    with pytest.raises(Failure, match="terminal"):
        settle(unknown, "released", 0)
    assert unknown.spent == 20
    assert settle(reserved, "released", 0).available == 100
    with pytest.raises(Failure, match="VERSION"):
        change(ledger, "other", {}, request="0:x", running=False)


def test_ticket_capacity_preserves_settlement_and_unknown_records():
    ledger = Ledger(128, verification_reserve=1)
    for index in range(128):
        ledger = reserve(ledger, f"0:{index}", 1, phase="verify")
    with pytest.raises(Failure, match="CAPACITY"):
        reserve(ledger, "0:more", 1)
    settled = settle(ledger, "unknown", 1, "0:0")
    assert len(settled.tickets) == 128 and settled.spent == 1


@given(
    st.lists(
        st.tuples(st.integers(1, 20), st.sampled_from(["settled", "unknown", "released"])),
        max_size=40,
    )
)
def test_reference_model_never_spends_reserved_funds_twice(operations):
    ledger = Ledger(100, verification_reserve=1)
    spent = 0
    for index, (ceiling, outcome) in enumerate(operations):
        before = ledger
        identity = f"0:{index}"
        if ceiling > 100 - spent:
            with pytest.raises(Failure, match="BUDGET"):
                reserve(ledger, identity, ceiling, phase="verify")
            assert ledger == before
            continue
        ledger = reserve(ledger, identity, ceiling, phase="verify")
        assert ledger.reserved == ceiling and ledger.available == 100 - spent - ceiling
        charged = ceiling if outcome == "unknown" else (ceiling // 2 if outcome == "settled" else 0)
        ledger = settle(ledger, outcome, charged, identity)
        spent += charged
        assert ledger.spent == spent and ledger.available == 100 - spent and ledger.reserved == 0


def test_decoder_invariants_reject_fabricated_balances():
    ticket = Ticket("0:t", "execute", 10, "a" * 64)
    for ledger in [
        Ledger(10, (ticket, ticket)),
        Ledger(10, (replace(ticket, phase="bad"),)),
        Ledger(10, (replace(ticket, status="bad"),)),
        Ledger(10, (replace(ticket, charged=1),)),
        Ledger(10, (replace(ticket, status="unknown", charged=0),)),
        Ledger(9, (ticket,)),
    ]:
        with pytest.raises(Failure):
            validate(replace(ledger, verification_reserve=1))


def test_verification_allocation_survives_other_phases_and_release():
    ledger = Ledger(100, verification_reserve=30)
    ledger = reserve(ledger, ceiling=70)
    for phase in ("generate", "execute", "repair"):
        with pytest.raises(Failure, match="BUDGET"):
            reserve(ledger, "0:another", 1, phase)
    verified = reserve(ledger, "0:verify", 30, "verify")
    assert verified.available == 0 and verified.protected_verification == 0
    released = settle(verified, "released", 0, "0:verify")
    assert released.protected_verification == 30 and released.available == 30
    with pytest.raises(Failure, match="BUDGET"):
        reserve(released, "0:another", 1)
    with pytest.raises(Failure, match="BINDING"):
        validate(Ledger(100, (Ticket("0:x", "verify", 1, "not-a-digest"),), 1))
    with pytest.raises(Failure, match="verification funds"):
        validate(Ledger(100, (Ticket("0:x", "execute", 90, "a" * 64),), 30))


def test_funding_cannot_be_self_authorized_or_cross_mission():
    runtime, command, keys = runtime_and_command()
    command = command | {
        "kind": "budget.configure",
        "payload": {
            "mission": "m",
            "budget": 100,
            "verification_reserve": 30,
        },
    }
    original = runtime.state_hash
    with pytest.raises(Failure, match="QUORUM"):
        runtime.apply(sign_command(command, {("a", 1): keys[("a", 1)]}), height=1)
    all_keys, _, _ = setup()
    with pytest.raises(Failure, match="AUTHORITY"):
        runtime.apply(
            sign_command(
                command | {"actor": "worker"},
                {
                    ("worker", 1): all_keys[("worker", 1)],
                },
            ),
            height=1,
        )
    with pytest.raises(Failure, match="SCOPE"):
        runtime.apply(
            sign_command(
                command
                | {
                    "payload": command["payload"]
                    | {
                        "mission": "other",
                    }
                },
                keys,
            ),
            height=1,
        )
    assert runtime.state_hash == original
