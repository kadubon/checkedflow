"""Bounded isolated-work ownership; reported completion is not artifact acceptance."""

from dataclasses import dataclass, replace
from typing import cast

from checkedflow.core.authority import Credential, Verified
from checkedflow.core.values import Object, fields, integer, names, require, text
from checkedflow.core.work_budget import Ledger, Ticket
from checkedflow.core.work_budget import change as settle_budget

MAX_TASKS = 128
ROLE = {"generate": "producer", "execute": "executor", "verify": "verifier", "repair": "producer"}
WORKER_COMMANDS = frozenset({"task.lease", "task.start", "task.heartbeat", "task.finish"})


@dataclass(frozen=True)
class Task:
    identity: str
    ticket: str
    workers: tuple[str, ...]
    lease_blocks: int
    expires: int
    max_attempts: int
    status: str = "ready"
    owner: str = ""
    revision: int = 0
    fence: int = 0
    until: int = 0
    started: int = 0
    evidence: str = ""
    reason: str = ""


def funding(ledger: Ledger, task: Task) -> Ticket:
    ticket = next((item for item in ledger.tickets if item.identity == task.ticket), None)
    require(ticket is not None, "STATE", "task funding missing")
    return cast(Ticket, ticket)


def _terminal(ledger: Ledger, task: Task, status: str, reason: str) -> tuple[Ledger, Task]:
    ticket = funding(ledger, task)
    outcome = (
        "released" if status == "cancelled" else ("unknown" if status == "unknown" else "settled")
    )
    updated = settle_budget(
        ledger,
        "budget.settle",
        {
            "mission": "internal",
            "ticket": task.ticket,
            "outcome": outcome,
            "charged": 0 if outcome == "released" else ticket.ceiling,
        },
        request=task.identity,
        running=False,
    )
    return updated, replace(task, status=status, reason=reason)


def advance(
    ledger: Ledger, tasks: tuple[Task, ...], height: int, credentials: tuple[Credential, ...]
) -> tuple[Ledger, tuple[Task, ...]]:
    result = []
    for task in tasks:
        if task.status in {"ready", "leased", "running"}:
            authority = task.status == "ready" or any(
                item.identity == task.owner
                and item.revision == task.revision
                and item.usable_at(height)
                for item in credentials
            )
            expired = height >= task.expires or (task.status != "ready" and height >= task.until)
            if expired or not authority:
                reason = "authority_unavailable" if not authority else "lease_expired"
                if task.status == "running":
                    ledger, task = _terminal(ledger, task, "unknown", reason)
                elif height >= task.expires or task.fence >= task.max_attempts:
                    ledger, task = _terminal(ledger, task, "cancelled", reason)
                else:
                    task = replace(
                        task, status="ready", owner="", revision=0, until=0, reason=reason
                    )
        result.append(task)
    return ledger, tuple(result)


def change(
    ledger: Ledger,
    tasks: tuple[Task, ...],
    kind: str,
    payload: Object,
    context: Verified,
    *,
    request: str,
    mode: str,
    mission: str,
    credentials: tuple[Credential, ...],
) -> tuple[Ledger, tuple[Task, ...]]:
    if kind == "task.admit":
        context.require_administration()
        fields(payload, "mission ticket workers lease_blocks expires max_attempts")
        require(mode == "running", "PAUSED", "mission is not admitting tasks")
        require(len(tasks) < MAX_TASKS, "CAPACITY", "task retention capacity")
        require(not any(item.identity == request for item in tasks), "DUPLICATE", "task exists")
        task = Task(
            request,
            text(payload["ticket"], limit=80),
            tuple(sorted(names(payload["workers"], limit=64))),
            integer(payload["lease_blocks"], low=1, high=10000),
            integer(payload["expires"], low=context.height + 1, high=context.height + 1000000),
            integer(payload["max_attempts"], low=1, high=8),
        )
        ticket = funding(ledger, task)
        require(ticket.status == "reserved", "BUDGET", "task ticket is not reserved")
        require(
            not any(item.ticket == task.ticket for item in tasks),
            "BUDGET",
            "ticket already attached",
        )
        require(bool(task.workers), "AUTHORITY", "explicit worker scope required")
        require(
            all(
                any(
                    credential.identity == worker
                    and credential.role == ROLE[ticket.phase]
                    and credential.mission == mission
                    and credential.usable_at(context.height)
                    for credential in credentials
                )
                for worker in task.workers
            ),
            "AUTHORITY",
            "worker scope or purpose differs",
        )
        return ledger, tuple(sorted((*tasks, task), key=lambda item: item.identity))
    if kind == "task.cancel":
        context.require_administration()
        fields(payload, "mission task")
    else:
        require(kind in WORKER_COMMANDS, "VERSION", "unsupported task command")
        fields(
            payload,
            "mission task"
            if kind == "task.lease"
            else (
                "mission task fence outcome evidence"
                if kind == "task.finish"
                else "mission task fence"
            ),
        )
    identity = text(payload["task"], limit=80)
    selected = next((item for item in tasks if item.identity == identity), None)
    require(selected is not None, "NOT_FOUND", "task missing")
    task = cast(Task, selected)
    require(task.status in {"ready", "leased", "running"}, "STATE", "task is terminal")
    ticket = funding(ledger, task)
    if kind == "task.cancel":
        ledger, task = _terminal(
            ledger, task, "unknown" if task.status == "running" else "cancelled", "cancelled"
        )
    else:
        context.require_role(ROLE[ticket.phase], mission)
        require(context.actor.identity in task.workers, "AUTHORITY", "worker not approved for task")
        if kind == "task.lease":
            require(mode == "running", "PAUSED", "mission is not dispatching")
            require(
                task.status == "ready" and task.fence < task.max_attempts,
                "LEASE",
                "task unavailable",
            )
            task = replace(
                task,
                status="leased",
                owner=context.actor.identity,
                revision=context.actor.revision,
                fence=task.fence + 1,
                until=min(context.height + task.lease_blocks, task.expires),
                reason="",
            )
        else:
            require(
                task.owner == context.actor.identity
                and task.revision == context.actor.revision
                and task.fence == integer(payload["fence"], low=1)
                and context.height < task.until,
                "FENCE",
                "attempt ownership is stale",
            )
            if kind == "task.start":
                require(mode == "running", "PAUSED", "mission is not starting work")
                require(task.status == "leased", "STATE", "task is not leased")
                task = replace(task, status="running", started=integer(context.height, low=1))
            else:
                require(task.status == "running", "STATE", "task has not started")
                if kind == "task.heartbeat":
                    require(mode in {"running", "draining"}, "PAUSED", "heartbeat inhibited")
                    task = replace(
                        task, until=min(context.height + task.lease_blocks, task.expires)
                    )
                else:
                    outcome = text(payload["outcome"])
                    require(outcome in {"reported", "unknown"}, "SHAPE", "unsupported task outcome")
                    evidence = text(payload["evidence"], limit=64)
                    require(
                        len(evidence) == 64 and all(c in "0123456789abcdef" for c in evidence),
                        "BINDING",
                        "evidence digest required",
                    )
                    ledger, task = _terminal(
                        ledger,
                        replace(task, evidence=evidence),
                        "finished" if outcome == "reported" else "unknown",
                        "result_" + outcome,
                    )
    return ledger, tuple(task if item.identity == identity else item for item in tasks)


def validate(ledger: Ledger, tasks: tuple[Task, ...]) -> None:
    require(len(tasks) <= MAX_TASKS, "CAPACITY", "task retention capacity")
    previous = ""
    tickets: set[str] = set()
    for task in tasks:
        text(task.identity, limit=80)
        require(previous < task.identity, "STATE", "tasks must be unique and sorted")
        previous = task.identity
        require(task.ticket not in tickets, "STATE", "task ticket used twice")
        tickets.add(task.ticket)
        ticket = funding(ledger, task)
        require(
            0 < len(task.workers) <= 64 and tuple(sorted(set(task.workers))) == task.workers,
            "STATE",
            "worker scope",
        )
        for worker in task.workers:
            text(worker, limit=80)
        integer(task.lease_blocks, low=1, high=10000)
        integer(task.expires, low=1)
        integer(task.max_attempts, low=1, high=8)
        integer(task.fence, high=task.max_attempts)
        integer(task.revision)
        integer(task.until, high=task.expires)
        integer(task.started, high=task.until)
        require(
            task.status in {"ready", "leased", "running", "finished", "unknown", "cancelled"},
            "STATE",
            "task status",
        )
        expected = {"finished": "settled", "unknown": "unknown", "cancelled": "released"}.get(
            task.status, "reserved"
        )
        require(ticket.status == expected, "STATE", "task and funding status differ")
        require(
            ticket.charged == (ticket.ceiling if expected in {"settled", "unknown"} else 0),
            "STATE",
            "task charge differs",
        )
        require(
            task.reason
            in {
                "",
                "lease_expired",
                "authority_unavailable",
                "cancelled",
                "result_reported",
                "result_unknown",
            },
            "STATE",
            "task reason",
        )
        if task.status == "ready":
            require(
                not task.owner and task.revision == task.until == task.started == 0,
                "STATE",
                "ready task owns attempt",
            )
        elif task.status != "cancelled" or task.owner:
            require(
                task.owner in task.workers
                and task.revision > 0
                and task.fence > 0
                and task.until > 0,
                "STATE",
                "missing attempt owner",
            )
        if task.status in {"running", "finished", "unknown"}:
            require(0 < task.started < task.until, "STATE", "missing or invalid execution start")
        if task.status == "cancelled" and not task.owner:
            require(task.revision == task.until == 0, "STATE", "ownerless cancellation has attempt")
        if task.status in {"ready", "leased", "cancelled"}:
            require(
                task.started == 0 and not task.evidence,
                "STATE",
                "unstarted task has execution evidence",
            )
        if task.evidence:
            require(
                len(task.evidence) == 64 and all(c in "0123456789abcdef" for c in task.evidence),
                "STATE",
                "invalid evidence digest",
            )
        require(
            task.status != "finished" or bool(task.evidence),
            "STATE",
            "finished task lacks evidence",
        )
        reasons = {
            "ready": {"", "lease_expired", "authority_unavailable"},
            "leased": {""},
            "running": {""},
            "finished": {"result_reported"},
            "unknown": {"lease_expired", "authority_unavailable", "cancelled", "result_unknown"},
            "cancelled": {"lease_expired", "authority_unavailable", "cancelled"},
        }
        require(task.reason in reasons[task.status], "STATE", "reason differs from task status")
        require(
            bool(task.evidence) == task.reason.startswith("result_"),
            "STATE",
            "evidence and result reason differ",
        )
