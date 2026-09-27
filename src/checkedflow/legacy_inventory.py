"""Read-only migration inventory anchored in a separately trusted v1 checkpoint.

This does not create successor authority, release reservations or stop old dispatch.
Keep the complete snapshot: summary fields are not a replacement for historical state.
"""

from dataclasses import dataclass

from checkedflow.core.values import integer, require, text
from checkedflow.serialization import decode, encode
from checkedflow.wire import digest, document, dumps


@dataclass(frozen=True)
class Checkpoint:
    """Operator-provisioned trust anchor, never taken from the supplied snapshot."""

    chain: str
    height: int
    state_hash: str


@dataclass(frozen=True)
class Balance:
    mission: str
    budget: int
    spent: int
    reserved: int
    available: int


@dataclass(frozen=True)
class Inventory:
    checkpoint: Checkpoint
    snapshot: bytes
    balances: tuple[Balance, ...]
    pending_tasks: tuple[str, ...]
    open_residuals: tuple[str, ...]
    withdrawn_capabilities: tuple[str, ...]


def inspect_snapshot(raw: bytes, trusted: Checkpoint) -> Inventory:
    """Authenticate and retain every legacy field without advancing committed time.

    The 4 MiB v1 state bound applies before parsing. The caller must obtain the
    checkpoint independently, for example from an authenticated old full node.
    Unknown attempts may already be charged; they remain pending independently
    of the reserved balance. No candidate code, network or persistence is used.
    """
    text(trusted.chain, limit=128)
    integer(trusted.height)
    require(
        len(trusted.state_hash) == 64
        and all(char in "0123456789abcdef" for char in trusted.state_hash),
        "CHECKPOINT",
        "invalid trusted state hash",
    )
    require(len(raw) <= 4_194_304, "LIMIT", "legacy snapshot byte limit")
    value = document(raw)
    require(digest(value) == trusted.state_hash, "CHECKPOINT", "legacy state hash differs")
    state = decode(value)
    require(
        state.chain == trusted.chain and state.height == trusted.height,
        "CHECKPOINT",
        "legacy chain or height differs",
    )
    require(encode(state) == value, "SHAPE", "legacy snapshot is not lossless")
    funded = dict.fromkeys(state.missions, 0)
    for task in state.tasks.values():
        require(task.mission in funded, "INVARIANT", "legacy task mission missing")
        if task.funded:
            funded[task.mission] += task.cost
    balances = []
    for identity, mission in sorted(state.missions.items()):
        available = mission.budget - mission.spent - mission.reserved
        require(available >= 0, "INVARIANT", "legacy budget exceeded")
        require(
            mission.reserved == funded[identity],
            "INVARIANT",
            "legacy reservations differ from funded tasks",
        )
        balances.append(
            Balance(identity, mission.budget, mission.spent, mission.reserved, available)
        )
    return Inventory(
        trusted,
        dumps(value),
        tuple(balances),
        tuple(
            identity
            for identity, task in sorted(state.tasks.items())
            if task.status in {"ready", "leased", "running", "uncertain"}
        ),
        tuple(
            identity
            for identity, residual in sorted(state.residuals.items())
            if residual.status == "open"
        ),
        tuple(
            identity
            for identity, capability in sorted(state.capabilities.items())
            if capability.status in {"revoked", "quarantined", "expired"}
        ),
    )
