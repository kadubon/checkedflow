"""Governed external-operation intent; consensus never sends provider requests.

An intent digest binds the complete provider contract. The executor must verify its
bytes, current own-node state and local freshness before using a separate credential.
Reservation means a send may have happened; uncertainty can never authorize another.
"""

from dataclasses import dataclass, replace
from typing import cast

from checkedflow.core.authority import Credential, Verified
from checkedflow.core.values import Object, fields, integer, require, text
from checkedflow.core.work_acceptance import Candidate, fingerprint, status
from checkedflow.core.work_budget import Ledger, Ticket
from checkedflow.core.work_budget import validate as validate_budget
from checkedflow.core.work_tasks import Task

MAX_EFFECTS = 64
EXECUTOR_COMMANDS = frozenset({"effect.reserve", "effect.report"})
COMMANDS = EXECUTOR_COMMANDS | {
    "effect.prepare",
    "effect.authorize",
    "effect.deny",
    "effect.reconcile",
}
UNRESOLVED = frozenset({"dispatch_reserved", "observed", "unknown", "compensation_required"})
STATUSES = UNRESOLVED | {"prepared", "authorized", "denied", "expired", "reconciled"}


@dataclass(frozen=True)
class Effect:
    identity: str
    operation: str
    candidate: str
    ticket: str
    intent: str
    policy: str
    executor: str
    revision: int
    prepared: int
    expires: int
    lease_blocks: int
    status: str = "prepared"
    authorization: str = ""
    authorized: int = 0
    fence: int = 0
    reserved: int = 0
    until: int = 0
    number: int = 0
    evidence: str = ""
    reason: str = ""


def _candidate(effect: Effect, candidates: tuple[Candidate, ...]) -> Candidate:
    found = next((item for item in candidates if item.identity == effect.candidate), None)
    require(found is not None, "DEPENDENCY", "effect pins its candidate")
    return cast(Candidate, found)


def _funding(effect: Effect, ledger: Ledger) -> Ticket:
    found = next((item for item in ledger.tickets if item.identity == effect.ticket), None)
    require(found is not None, "DEPENDENCY", "effect pins its funding")
    return cast(Ticket, found)


def _loss(
    effect: Effect,
    candidates: tuple[Candidate, ...],
    credentials: tuple[Credential, ...],
    height: int,
) -> str:
    if height >= effect.expires:
        return "expired"
    if status(_candidate(effect, candidates), credentials, height) != "accepted":
        return "candidate_unusable"
    if not any(
        item.identity == effect.executor
        and item.revision == effect.revision
        and item.usable_at(height)
        for item in credentials
    ):
        return "credential_unusable"
    return ""


def _charge(ledger: Ledger, effect: Effect, outcome: str) -> Ledger:
    ticket = _funding(effect, ledger)
    updated = replace(
        ticket, status=outcome, charged=0 if outcome == "released" else ticket.ceiling
    )
    result = replace(
        ledger,
        tickets=tuple(
            updated if item.identity == ticket.identity else item for item in ledger.tickets
        ),
    )
    validate_budget(result)
    return result


def advance(
    ledger: Ledger,
    effects: tuple[Effect, ...],
    candidates: tuple[Candidate, ...],
    credentials: tuple[Credential, ...],
    height: int,
) -> tuple[Ledger, tuple[Effect, ...]]:
    result = []
    for effect in effects:
        loss = _loss(effect, candidates, credentials, height)
        if effect.status in {"prepared", "authorized"} and loss:
            effect = replace(effect, status="expired", reason=loss)
            ledger = _charge(ledger, effect, "released")
        elif effect.status == "dispatch_reserved" and (loss or height >= effect.until):
            effect = replace(effect, status="unknown", reason=loss or "dispatch_deadline")
        elif effect.status in {"observed", "reconciled"} and (
            status(_candidate(effect, candidates), credentials, height) != "accepted"
            or (
                effect.status == "observed"
                and any(
                    item.identity == effect.executor
                    and item.revision == effect.revision
                    and item.revoked
                    for item in credentials
                )
            )
        ):
            effect = replace(effect, status="compensation_required", reason="adverse_evidence")
        result.append(effect)
    return ledger, tuple(result)


def change(
    ledger: Ledger,
    effects: tuple[Effect, ...],
    kind: str,
    payload: Object,
    context: Verified,
    *,
    request: str,
    mode: str,
    mission: str,
    candidates: tuple[Candidate, ...],
    tasks: tuple[Task, ...],
    credentials: tuple[Credential, ...],
) -> tuple[Ledger, tuple[Effect, ...]]:
    require(kind in COMMANDS, "VERSION", "unsupported effect command")
    if kind == "effect.prepare":
        context.require_administration()
        fields(
            payload, "mission candidate ticket intent policy executor revision expires lease_blocks"
        )
        require(mode == "running", "PAUSED", "mission is not admitting effects")
        require(len(effects) < MAX_EFFECTS, "CAPACITY", "effect retention capacity")
        effect = Effect(
            request,
            fingerprint(context.command_digest),
            text(payload["candidate"], limit=80),
            text(payload["ticket"], limit=80),
            fingerprint(payload["intent"]),
            fingerprint(payload["policy"]),
            text(payload["executor"], limit=80),
            integer(payload["revision"], low=1),
            context.height,
            integer(payload["expires"], low=context.height + 1, high=context.height + 1000000),
            integer(payload["lease_blocks"], low=1, high=10000),
        )
        require(
            not any(
                item.identity == request
                or item.intent == effect.intent
                or item.operation == effect.operation
                for item in effects
            ),
            "DUPLICATE",
            "retained effect identity or intent exists",
        )
        require(
            not any(item.ticket == effect.ticket for item in effects)
            and not any(item.ticket == effect.ticket for item in tasks),
            "BUDGET",
            "effect funding already attached",
        )
        ticket = _funding(effect, ledger)
        require(
            ticket.status == "reserved"
            and ticket.phase == "execute"
            and ticket.target == effect.intent,
            "BUDGET",
            "effect requires unused intent-bound execution funding",
        )
        require(
            not _loss(effect, candidates, credentials, context.height),
            "AUTHORITY",
            "effect is not currently eligible",
        )
        effects = tuple(sorted((*effects, effect), key=lambda item: item.identity))
    else:
        fields(
            payload,
            "mission effect fence outcome number evidence"
            if kind == "effect.report"
            else (
                "mission effect outcome number evidence"
                if kind == "effect.reconcile"
                else "mission effect"
            ),
        )
        found = next((item for item in effects if item.identity == payload["effect"]), None)
        require(found is not None, "NOT_FOUND", "effect missing")
        effect = cast(Effect, found)
        if kind in EXECUTOR_COMMANDS:
            context.require_role("effect_executor", mission)
            require(
                context.actor.identity == effect.executor
                and context.actor.revision == effect.revision,
                "AUTHORITY",
                "effect executor revision differs",
            )
        else:
            context.require_administration()
        if kind == "effect.authorize":
            require(mode == "running", "PAUSED", "mission is not authorizing effects")
            require(effect.status == "prepared", "STATE", "effect is not prepared")
            effect = replace(
                effect,
                status="authorized",
                authorization=fingerprint(context.command_digest),
                authorized=context.height,
            )
        elif kind == "effect.deny":
            require(
                effect.status in {"prepared", "authorized"},
                "STATE",
                "possibly sent effects cannot be denied",
            )
            effect = replace(effect, status="denied", reason="governed_denial")
            ledger = _charge(ledger, effect, "released")
        elif kind == "effect.reserve":
            require(mode == "running", "PAUSED", "mission is not dispatching effects")
            require(
                effect.status == "authorized",
                "STATE",
                "effect is not authorized for a first dispatch",
            )
            effect = replace(
                effect,
                status="dispatch_reserved",
                fence=1,
                reserved=context.height,
                until=min(effect.expires, context.height + effect.lease_blocks),
            )
            ledger = _charge(ledger, effect, "unknown")
        else:
            if kind == "effect.report":
                require(
                    effect.status == "dispatch_reserved",
                    "STATE",
                    "live original reservation required",
                )
                require(
                    integer(payload["fence"], low=1) == effect.fence,
                    "FENCE",
                    "effect fence differs",
                )
            else:
                require(
                    effect.status in UNRESOLVED | {"reconciled"},
                    "STATE",
                    "effect has never been reserved",
                )
            outcome = text(payload["outcome"])
            require(outcome in {"observed", "unknown"}, "SHAPE", "absence cannot authorize retry")
            evidence = fingerprint(payload["evidence"])
            number = integer(payload["number"], low=1 if outcome == "observed" else 0)
            require(
                outcome == "observed" or number == 0,
                "SHAPE",
                "unknown reports cannot assert an object",
            )
            require(
                not number or effect.number in {0, number},
                "CONFLICT",
                "original provider object changed",
            )
            classification = "unknown"
            if outcome == "observed":
                classification = "observed" if kind == "effect.report" else "reconciled"
                if (
                    status(_candidate(effect, candidates), credentials, context.height)
                    != "accepted"
                ):
                    classification = "compensation_required"
            effect = replace(
                effect,
                status=classification,
                number=number or effect.number,
                evidence=evidence,
                reason="provider_uncertain" if outcome == "unknown" else "",
            )
            ledger = _charge(ledger, effect, "unknown" if outcome == "unknown" else "settled")
        effects = tuple(effect if item.identity == effect.identity else item for item in effects)
    validate(ledger, effects, candidates, tasks, credentials, mission, context.height)
    return ledger, effects


def validate(
    ledger: Ledger,
    effects: tuple[Effect, ...],
    candidates: tuple[Candidate, ...],
    tasks: tuple[Task, ...],
    credentials: tuple[Credential, ...],
    mission: str,
    height: int,
) -> None:
    require(len(effects) <= MAX_EFFECTS, "CAPACITY", "effect retention capacity")
    previous = ""
    tickets = {item.ticket for item in tasks}
    intents: set[str] = set()
    operations: set[str] = set()
    for effect in effects:
        text(effect.identity, limit=80)
        require(previous < effect.identity, "STATE", "effect identities must be unique and sorted")
        previous = effect.identity
        for value in (effect.operation, effect.intent, effect.policy):
            fingerprint(value)
        require(
            effect.intent not in intents and effect.operation not in operations,
            "DUPLICATE",
            "duplicate effect intent or operation",
        )
        intents.add(effect.intent)
        operations.add(effect.operation)
        require(effect.ticket not in tickets, "BUDGET", "effect funding is shared")
        tickets.add(effect.ticket)
        ticket = _funding(effect, ledger)
        require(
            ticket.phase == "execute" and ticket.target == effect.intent,
            "BINDING",
            "effect funding differs",
        )
        candidate = _candidate(effect, candidates)
        integer(effect.prepared, low=candidate.admitted, high=height)
        integer(effect.expires, low=effect.prepared + 1, high=effect.prepared + 1000000)
        integer(effect.lease_blocks, low=1, high=10000)
        integer(effect.revision, low=1)
        require(
            any(
                item.identity == effect.executor
                and item.revision == effect.revision
                and item.role == "effect_executor"
                and item.mission == mission
                and item.activated_height <= effect.prepared
                and (item.retired_height is None or effect.prepared < item.retired_height)
                for item in credentials
            ),
            "AUTHORITY",
            "effect executor purpose, scope or history differs",
        )
        require(effect.status in STATUSES, "STATE", "unsupported effect state")
        integer(effect.authorized, high=height)
        if effect.authorization:
            fingerprint(effect.authorization)
            require(
                effect.status != "prepared" and effect.prepared <= effect.authorized,
                "STATE",
                "authorization predates preparation or remains unapplied",
            )
        else:
            require(
                effect.authorized == 0 and effect.status in {"prepared", "denied", "expired"},
                "STATE",
                "missing effect authorization",
            )
        integer(effect.fence, high=1)
        integer(effect.reserved, high=height)
        integer(effect.until)
        integer(effect.number)
        require(
            isinstance(effect.reason, str) and len(effect.reason) <= 80,
            "SHAPE",
            "bounded effect reason",
        )
        if effect.evidence:
            fingerprint(effect.evidence)
        require(
            effect.number == 0 or bool(effect.evidence),
            "EVIDENCE",
            "object identity requires evidence",
        )
        require(
            effect.status not in {"denied", "expired", "unknown"} or bool(effect.reason),
            "EVIDENCE",
            "unresolved or denied effect requires a reason",
        )
        require(
            effect.status not in {"prepared", "authorized", "dispatch_reserved"}
            or (not effect.reason and not effect.evidence and effect.number == 0),
            "STATE",
            "unobserved effect carries provider evidence",
        )
        if effect.fence:
            require(
                bool(effect.authorization)
                and effect.authorized
                <= effect.reserved
                < effect.until
                == min(effect.expires, effect.reserved + effect.lease_blocks),
                "STATE",
                "invalid dispatch window",
            )
            require(
                effect.status in UNRESOLVED | {"reconciled"},
                "STATE",
                "reserved effect cannot become unstarted",
            )
            expected = "unknown" if effect.status in {"dispatch_reserved", "unknown"} else "settled"
            require(
                ticket.status == expected and ticket.charged == ticket.ceiling,
                "BUDGET",
                "possible send must retain full charge",
            )
            if effect.status in {"observed", "reconciled", "compensation_required"}:
                require(
                    effect.number > 0 and bool(effect.evidence),
                    "EVIDENCE",
                    "observed effect needs identity and evidence",
                )
        else:
            require(
                effect.status in {"prepared", "authorized", "denied", "expired"}
                and effect.reserved == effect.until == effect.number == 0
                and not effect.evidence,
                "STATE",
                "unreserved effect contains send evidence",
            )
            require(
                ticket.status
                == ("released" if effect.status in {"denied", "expired"} else "reserved")
                and ticket.charged == 0,
                "BUDGET",
                "unstarted effect funding differs",
            )
