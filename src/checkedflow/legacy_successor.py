"""Prepare a paused successor with conserved legacy funding, without activating nodes."""

from dataclasses import replace

from checkedflow.core.operational import State, genesis
from checkedflow.core.values import obj, require
from checkedflow.core.work_budget import Inheritance, Ledger, LegacyObligation
from checkedflow.legacy_inventory import Checkpoint, inspect_snapshot
from checkedflow.operational_codec import decode, state_bytes
from checkedflow.serialization import decode as decode_legacy
from checkedflow.wire import digest, document


def prepare(raw: bytes, trusted: Checkpoint, initial: State, *, mission: str) -> State:
    """Bind one old mission to a pristine paused successor and hold all old liabilities.

    The operator must retain the complete old snapshot and signed history. This
    constructor neither authenticates a cutover approval nor stops an old node.
    Its output is a preparation artifact, not authorization to activate validators.
    Uncertain old work is never made into a new executable task.
    """
    inventory = inspect_snapshot(raw, trusted)
    old = decode_legacy(document(inventory.snapshot))
    require(mission in old.missions, "NOT_FOUND", "legacy mission missing")
    require(initial.chain != old.chain, "CHAIN", "successor must use a new chain")
    fresh = genesis(
        initial.chain,
        initial.mission,
        initial.organizations,
        initial.credentials,
        limits=initial.journal.limits,
    )
    require(initial == fresh, "STATE", "successor must be a pristine paused genesis")
    source = old.missions[mission]
    task_values = obj(document(inventory.snapshot)["tasks"])
    available = source.budget - source.spent - source.reserved
    budget = Ledger(
        source.budget,
        verification_reserve=min(source.verification_reserve, available),
        inheritance=Inheritance(
            old.chain,
            mission,
            old.height,
            trusted.state_hash,
            source.budget,
            source.spent,
            source.reserved,
            tuple(
                LegacyObligation(identity, digest(task_values[identity]), task.cost, task.funded)
                for identity, task in sorted(old.tasks.items())
                if task.mission == mission and (task.funded or task.status == "uncertain")
            ),
        ),
    )
    # Exercise the actual startup codec and semantic invariants before returning.
    return decode(state_bytes(replace(fresh, budget=budget)))
