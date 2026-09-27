"""Bounded mission funding; accounting statements do not establish task correctness."""

from dataclasses import dataclass, replace
from typing import cast

from checkedflow.core.values import MAX_INT, Object, fields, integer, require, text

MAX_TICKETS = 128
PHASES = frozenset({"generate", "execute", "verify", "repair"})


@dataclass(frozen=True)
class Ticket:
    identity: str
    phase: str
    ceiling: int
    target: str
    status: str = "reserved"
    charged: int = 0


@dataclass(frozen=True)
class Inheritance:
    """Original v1 charges and held reservations, bound to retained historical state.

    These are not ordinary tickets. Current settlement and retirement commands
    cannot release them or overwrite their historical meaning.
    """

    chain: str
    mission: str
    height: int
    state_hash: str
    budget: int
    spent: int
    reserved: int


@dataclass(frozen=True)
class Ledger:
    budget: int = 0
    tickets: tuple[Ticket, ...] = ()
    verification_reserve: int = 0
    archived_spent: int = 0
    archived_verification: int = 0
    inheritance: Inheritance | None = None

    @property
    def reserved(self) -> int:
        inherited = 0 if self.inheritance is None else self.inheritance.reserved
        return inherited + sum(
            ticket.ceiling for ticket in self.tickets if ticket.status == "reserved"
        )

    @property
    def spent(self) -> int:
        inherited = 0 if self.inheritance is None else self.inheritance.spent
        return inherited + self.archived_spent + sum(ticket.charged for ticket in self.tickets)

    @property
    def available(self) -> int:
        return self.budget - self.reserved - self.spent

    @property
    def protected_verification(self) -> int:
        allocated = self.archived_verification + sum(
            ticket.ceiling if ticket.status == "reserved" else ticket.charged
            for ticket in self.tickets
            if ticket.phase == "verify"
        )
        return max(0, self.verification_reserve - allocated)


def validate(ledger: Ledger) -> None:
    integer(ledger.budget)
    if ledger.inheritance is not None:
        inherited = ledger.inheritance
        text(inherited.chain, limit=128)
        text(inherited.mission)
        integer(inherited.height)
        require(
            len(inherited.state_hash) == 64
            and all(char in "0123456789abcdef" for char in inherited.state_hash),
            "BINDING",
            "inherited state digest required",
        )
        integer(inherited.budget, low=1)
        integer(inherited.spent, high=inherited.budget)
        integer(inherited.reserved, high=inherited.budget - inherited.spent)
        require(ledger.budget == inherited.budget, "BUDGET", "inherited allowance changed")
    integer(ledger.archived_spent, high=ledger.budget)
    integer(ledger.archived_verification, high=ledger.archived_spent)
    integer(
        ledger.verification_reserve,
        low=1 if ledger.budget and ledger.inheritance is None else 0,
        high=ledger.budget,
    )
    require(len(ledger.tickets) <= MAX_TICKETS, "CAPACITY", "budget ticket capacity")
    previous = ""
    for ticket in ledger.tickets:
        text(ticket.identity, limit=80)
        require(previous < ticket.identity, "STATE", "budget tickets must be unique and sorted")
        previous = ticket.identity
        require(ticket.phase in PHASES, "STATE", "unsupported budget phase")
        integer(ticket.ceiling, low=1)
        require(
            len(ticket.target) == 64 and all(c in "0123456789abcdef" for c in ticket.target),
            "BINDING",
            "canonical target digest required",
        )
        integer(ticket.charged, high=ticket.ceiling)
        require(
            ticket.status in {"reserved", "settled", "unknown", "released"},
            "STATE",
            "budget status",
        )
        require(
            (ticket.status not in {"reserved", "released"} or ticket.charged == 0)
            and (ticket.status != "unknown" or ticket.charged == ticket.ceiling),
            "STATE",
            "budget charge inconsistent with status",
        )
    require(0 <= ledger.spent + ledger.reserved <= ledger.budget, "BUDGET", "budget exceeded")
    require(
        ledger.available >= ledger.protected_verification, "BUDGET", "verification funds consumed"
    )


def change(ledger: Ledger, kind: str, payload: Object, *, request: str, running: bool) -> Ledger:
    """Called only after current administrative quorum and mission checks."""
    validate(ledger)
    if kind == "budget.configure":
        fields(payload, "mission budget verification_reserve")
        require(ledger.budget == 0 and not ledger.tickets, "STATE", "budget already configured")
        budget = integer(payload["budget"], low=1, high=MAX_INT)
        result = Ledger(
            budget,
            verification_reserve=integer(payload["verification_reserve"], low=1, high=budget),
        )
    elif kind == "budget.reserve":
        fields(payload, "mission phase ceiling target")
        require(running, "PAUSED", "mission is not admitting work")
        phase = text(payload["phase"])
        require(phase in PHASES, "SHAPE", "unsupported budget phase")
        ceiling = integer(payload["ceiling"], low=1)
        target = text(payload["target"], limit=64)
        require(len(ledger.tickets) < MAX_TICKETS, "CAPACITY", "budget ticket capacity")
        require(
            not any(ticket.identity == request for ticket in ledger.tickets),
            "DUPLICATE",
            "ticket exists",
        )
        usable = ledger.available - (ledger.protected_verification if phase != "verify" else 0)
        require(ceiling <= usable, "BUDGET", "insufficient unreserved budget")
        result = replace(
            ledger,
            tickets=tuple(
                sorted(
                    (*ledger.tickets, Ticket(request, phase, ceiling, target)),
                    key=lambda ticket: ticket.identity,
                )
            ),
        )
    else:
        require(kind == "budget.settle", "VERSION", "unsupported budget command")
        fields(payload, "mission ticket outcome charged")
        identity = text(payload["ticket"], limit=80)
        selected = next((ticket for ticket in ledger.tickets if ticket.identity == identity), None)
        require(selected is not None, "NOT_FOUND", "budget ticket missing")
        selected = cast(Ticket, selected)
        require(selected.status == "reserved", "STATE", "budget ticket already terminal")
        outcome = text(payload["outcome"])
        require(outcome in {"settled", "unknown", "released"}, "SHAPE", "unsupported settlement")
        charged = integer(payload["charged"], high=selected.ceiling)
        require(
            outcome != "unknown" or charged == selected.ceiling,
            "BUDGET",
            "unknown must charge ceiling",
        )
        require(outcome != "released" or charged == 0, "BUDGET", "release cannot charge")
        updated = replace(selected, status=outcome, charged=charged)
        result = replace(
            ledger,
            tickets=tuple(
                updated if ticket.identity == identity else ticket for ticket in ledger.tickets
            ),
        )
    validate(result)
    return result
