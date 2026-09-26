"""Governed retirement of settled, unreferenced work; unknown obligations stay active."""

import json
from dataclasses import asdict, dataclass, replace
from hashlib import sha256

from checkedflow.core.values import Object, fields, integer, names, require
from checkedflow.core.work_acceptance import Candidate, fingerprint
from checkedflow.core.work_budget import Ledger
from checkedflow.core.work_budget import validate as validate_budget
from checkedflow.core.work_tasks import Task

DOMAIN = b"CheckedFlow/work-archive/v1\x00"
MAX_ARCHIVE_BYTES = 1048576


@dataclass(frozen=True)
class Head:
    sequence: int = 0
    root: str = "0" * 64

    def __post_init__(self) -> None:
        integer(self.sequence)
        fingerprint(self.root)
        require(self.sequence != 0 or self.root == "0" * 64, "STATE", "initial work archive root")


@dataclass(frozen=True)
class WorkArchive:
    previous_root: str
    sequence: int
    body: bytes

    @property
    def root(self) -> str:
        fingerprint(self.previous_root)
        integer(self.sequence, low=1)
        require(len(self.body) <= MAX_ARCHIVE_BYTES, "LIMIT", "work archive byte ceiling")
        return sha256(
            DOMAIN
            + bytes.fromhex(self.previous_root)
            + self.sequence.to_bytes(8, "big")
            + self.body
        ).hexdigest()


def retire(
    head: Head,
    ledger: Ledger,
    tasks: tuple[Task, ...],
    candidates: tuple[Candidate, ...],
    payload: Object,
    *,
    epoch: int,
    height: int,
    chain: str,
    mission: str,
    mode: str,
) -> tuple[Head, Ledger, tuple[Task, ...], tuple[Candidate, ...], WorkArchive]:
    """Caller has authenticated current three-organization administration and mission scope."""
    fields(payload, "mission expected_root tickets tasks candidates")
    require(mode == "paused", "MAINTENANCE", "pause before work retirement")
    require(payload["expected_root"] == head.root, "CONFLICT", "work archive predecessor changed")
    selected_tickets = set(names(payload["tickets"], limit=128))
    selected_tasks = set(names(payload["tasks"], limit=128))
    selected_candidates = set(names(payload["candidates"], limit=64))
    require(
        bool(selected_tickets or selected_tasks or selected_candidates),
        "SHAPE",
        "nonempty retirement batch required",
    )
    for identities, records in (
        (selected_tickets, ledger.tickets),
        (selected_tasks, tasks),
        (selected_candidates, candidates),
    ):
        require(
            identities <= {item.identity for item in records}, "NOT_FOUND", "archive record missing"
        )
        for identity in identities:
            prefix, _, _ = identity.partition(":")
            require(prefix.isascii() and prefix.isdecimal(), "EPOCH", "record epoch required")
            require(
                int(prefix) < epoch, "EPOCH", "retire the request epoch before its work records"
            )
    for candidate in candidates:
        if candidate.identity in selected_candidates:
            require(
                candidate.revoked or height >= candidate.expires, "DEPENDENCY", "live candidate"
            )
            require(
                all(
                    task.status in {"finished", "cancelled"}
                    for task in tasks
                    if task.identity in candidate.checks
                ),
                "UNRESOLVED",
                "candidate has unfinished or uncertain checks",
            )
        else:
            require(
                not selected_tasks.intersection(candidate.checks),
                "DEPENDENCY",
                "retained candidate pins checks",
            )
    for task in tasks:
        if task.identity in selected_tasks:
            require(
                task.status in {"finished", "cancelled"},
                "UNRESOLVED",
                "unfinished or uncertain task",
            )
        else:
            require(task.ticket not in selected_tickets, "DEPENDENCY", "retained task pins funding")
    removed = tuple(ticket for ticket in ledger.tickets if ticket.identity in selected_tickets)
    require(
        all(ticket.status in {"settled", "released"} for ticket in removed),
        "UNRESOLVED",
        "reserved or uncertain funding cannot retire",
    )
    result = replace(
        ledger,
        tickets=tuple(
            ticket for ticket in ledger.tickets if ticket.identity not in selected_tickets
        ),
        archived_spent=ledger.archived_spent + sum(ticket.charged for ticket in removed),
        archived_verification=ledger.archived_verification
        + sum(ticket.charged for ticket in removed if ticket.phase == "verify"),
    )
    validate_budget(result)
    body = {
        "chain": chain,
        "mission": mission,
        "height": height,
        "tickets": [asdict(ticket) for ticket in removed],
        "tasks": [asdict(task) for task in tasks if task.identity in selected_tasks],
        "candidates": [
            asdict(candidate)
            for candidate in candidates
            if candidate.identity in selected_candidates
        ],
    }
    # All object keys are fixed ASCII identifiers; this integer-only representation equals JCS.
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    archive = WorkArchive(head.root, head.sequence + 1, raw)
    return (
        Head(archive.sequence, archive.root),
        result,
        tuple(task for task in tasks if task.identity not in selected_tasks),
        tuple(
            candidate for candidate in candidates if candidate.identity not in selected_candidates
        ),
        archive,
    )
