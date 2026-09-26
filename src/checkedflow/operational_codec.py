"""Bounded control-state serialization; structure/integrity is not bootstrap authority."""

import json
from dataclasses import asdict, replace
from typing import cast

from checkedflow.core.authority import Credential
from checkedflow.core.key_registry import MAX_REVISIONS, roots
from checkedflow.core.key_registry import validate as validate_registry
from checkedflow.core.operational import State, genesis
from checkedflow.core.request_journal import Archive, Journal, Limits, Receipt, admit
from checkedflow.core.values import JSON, Object, array, fields, integer, names, obj, require, text
from checkedflow.core.work_acceptance import MAX_CANDIDATES, Candidate, Observation
from checkedflow.core.work_acceptance import validate as validate_candidates
from checkedflow.core.work_budget import MAX_TICKETS, Ledger, Ticket
from checkedflow.core.work_budget import validate as validate_budget
from checkedflow.core.work_tasks import MAX_TASKS, ROLE, Task
from checkedflow.core.work_tasks import advance as advance_tasks
from checkedflow.core.work_tasks import validate as validate_tasks
from checkedflow.wire import document, dumps, validate

MAX_STATE_BYTES = 4194304


def encode(state: State) -> Object:
    return obj(validate(json.loads(json.dumps(asdict(state)))))


def state_bytes(state: State) -> bytes:
    raw = dumps(encode(state))
    require(len(raw) <= MAX_STATE_BYTES, "LIMIT", "control-state byte ceiling")
    return raw


def _boolean(value: JSON) -> bool:
    require(type(value) is bool, "SHAPE", "boolean required")
    return cast(bool, value)


def _string(value: JSON) -> str:
    require(isinstance(value, str), "SHAPE", "string required")
    return cast(str, value)


def _receipt(value: JSON) -> Receipt:
    record = obj(value)
    fields(record, "request actor nonce command_digest administrative")
    receipt = Receipt(
        text(record["request"], limit=80),
        text(record["actor"], limit=80),
        integer(record["nonce"], low=1),
        text(record["command_digest"]),
        _boolean(record["administrative"]),
    )
    receipt.encoded()
    return receipt


def decode(raw: bytes) -> State:
    require(len(raw) <= MAX_STATE_BYTES, "LIMIT", "control-state byte ceiling")
    value = document(raw)
    fields(
        value,
        "chain mission organizations credentials journal height mode profile "
        "budget tasks candidates",
    )
    require(value["profile"] == "checkedflow/control-state/v2", "VERSION", "state profile")
    credentials = []
    for item in array(value["credentials"], limit=MAX_REVISIONS):
        record = obj(item)
        fields(
            record,
            "identity revision organization role mission public_key "
            "activated_height retired_height revoked",
        )
        retired = record["retired_height"]
        credentials.append(
            Credential(
                text(record["identity"]),
                integer(record["revision"], low=1),
                text(record["organization"]),
                text(record["role"]),
                _string(record["mission"]),
                text(record["public_key"]),
                integer(record["activated_height"]),
                None if retired is None else integer(retired),
                _boolean(record["revoked"]),
            )
        )
    height = integer(value["height"])
    validate_registry(tuple(credentials), height)
    journal = obj(value["journal"])
    fields(journal, "actors limits epoch archive_root receipts")
    limits = obj(journal["limits"])
    fields(limits, "ordinary_count administrative_count ordinary_bytes administrative_bytes")
    initial = genesis(
        text(value["chain"]),
        text(value["mission"]),
        names(value["organizations"], limit=4),
        roots(tuple(credentials)),
        limits=Limits(
            integer(limits["ordinary_count"]),
            integer(limits["administrative_count"]),
            integer(limits["ordinary_bytes"]),
            integer(limits["administrative_bytes"]),
        ),
    )
    slots = []
    for item in array(journal["actors"], limit=64):
        slot = array(item, limit=2)
        require(len(slot) == 2, "STATE", "actor slot shape")
        slots.append((text(slot[0]), integer(slot[1])))
    require(
        tuple(actor for actor, _ in slots) == tuple(actor for actor, _ in initial.journal.actors),
        "STATE",
        "actor registry differs",
    )
    epoch = integer(journal["epoch"])
    root = text(journal["archive_root"])
    # Validates the root representation without treating its self-consistency as authority.
    _ = Archive(root, epoch, ()).root
    require(epoch != 0 or root == "0" * 64, "STATE", "initial archive root differs")
    receipts = tuple(_receipt(item) for item in array(journal["receipts"], limit=4160))
    prior_nonces = dict(slots)
    for receipt in reversed(receipts):
        require(
            prior_nonces.get(receipt.actor) == receipt.nonce, "NONCE", "receipt nonce continuity"
        )
        prior_nonces[receipt.actor] -= 1
    reconstructed = Journal(
        tuple(sorted(prior_nonces.items())), initial.journal.limits, epoch, root
    )
    for receipt in receipts:
        reconstructed, duplicate = admit(reconstructed, receipt)
        require(not duplicate, "STATE", "duplicate stored receipt")
    mode = text(value["mode"])
    require(mode in {"paused", "running", "draining"}, "STATE", "unsupported control mode")
    budget_value = obj(value["budget"])
    fields(budget_value, "budget tickets verification_reserve")
    tickets = []
    for item in array(budget_value["tickets"], limit=MAX_TICKETS):
        ticket = obj(item)
        fields(ticket, "identity phase ceiling target status charged")
        tickets.append(
            Ticket(
                text(ticket["identity"], limit=80),
                text(ticket["phase"]),
                integer(ticket["ceiling"], low=1),
                text(ticket["target"], limit=64),
                text(ticket["status"]),
                integer(ticket["charged"]),
            )
        )
    budget = Ledger(
        integer(budget_value["budget"]),
        tuple(tickets),
        integer(budget_value["verification_reserve"]),
    )
    validate_budget(budget)
    tasks = []
    for item in array(value["tasks"], limit=MAX_TASKS):
        task = obj(item)
        fields(
            task,
            "identity ticket workers lease_blocks expires max_attempts status owner "
            "revision fence until started evidence reason",
        )
        tasks.append(
            Task(
                text(task["identity"], limit=80),
                text(task["ticket"], limit=80),
                names(task["workers"], limit=64),
                integer(task["lease_blocks"]),
                integer(task["expires"]),
                integer(task["max_attempts"]),
                text(task["status"]),
                _string(task["owner"]),
                integer(task["revision"]),
                integer(task["fence"]),
                integer(task["until"]),
                integer(task["started"]),
                _string(task["evidence"]),
                _string(task["reason"]),
            )
        )
    validate_tasks(budget, tuple(tasks))
    require(
        all(work.started <= height for work in tasks), "STATE", "execution start is in the future"
    )
    require(
        advance_tasks(budget, tuple(tasks), height, tuple(credentials)) == (budget, tuple(tasks)),
        "STATE",
        "unapplied task expiration or authority loss",
    )
    tickets_by_id = {ticket.identity: ticket for ticket in budget.tickets}
    for work in tasks:
        role = ROLE[tickets_by_id[work.ticket].phase]
        require(
            not work.owner
            or any(
                credential.identity == work.owner
                and credential.revision == work.revision
                and credential.role == role
                and credential.mission == initial.mission
                for credential in credentials
            ),
            "STATE",
            "task owner revision or purpose missing",
        )
        require(
            all(
                any(
                    credential.identity == worker
                    and credential.role == role
                    and credential.mission == initial.mission
                    for credential in credentials
                )
                for worker in work.workers
            ),
            "STATE",
            "task worker purpose or scope differs",
        )
    candidates = []
    for item in array(value["candidates"], limit=MAX_CANDIDATES):
        candidate = obj(item)
        fields(candidate, "identity target artifact admitted expires checks observations revoked")
        observations = []
        for row in array(candidate["observations"], limit=12):
            observation = obj(row)
            fields(
                observation, "task actor revision organization height evidence verdict withdrawn"
            )
            observations.append(
                Observation(
                    text(observation["task"], limit=80),
                    text(observation["actor"], limit=80),
                    integer(observation["revision"], low=1),
                    text(observation["organization"], limit=80),
                    integer(observation["height"]),
                    text(observation["evidence"], limit=64),
                    text(observation["verdict"]),
                    integer(observation["withdrawn"]),
                )
            )
        candidates.append(
            Candidate(
                text(candidate["identity"], limit=80),
                text(candidate["target"], limit=64),
                text(candidate["artifact"], limit=64),
                integer(candidate["admitted"]),
                integer(candidate["expires"]),
                names(candidate["checks"], limit=4),
                tuple(observations),
                _boolean(candidate["revoked"]),
            )
        )
    validate_candidates(
        tuple(candidates), budget, tuple(tasks), tuple(credentials), initial.mission, height
    )
    ordinary_ids = (
        {ticket.identity for ticket in budget.tickets}
        | {task.identity for task in tasks}
        | {candidate.identity for candidate in candidates}
    )
    administrators = {
        credential.identity for credential in credentials if credential.role == "administrator"
    }
    require(
        all(
            receipt.administrative
            == (receipt.actor in administrators and receipt.request not in ordinary_ids)
            for receipt in receipts
        ),
        "STATE",
        "receipt class differs from budget admission",
    )
    state = replace(
        initial,
        credentials=tuple(credentials),
        journal=reconstructed,
        height=height,
        mode=mode,
        budget=budget,
        tasks=tuple(tasks),
        candidates=tuple(candidates),
    )
    require(encode(state) == value, "STATE", "noncanonical control-state structure")
    return state


def archive_bytes(archive: Archive) -> bytes:
    _ = archive.root
    raw = dumps(obj(validate(json.loads(json.dumps(asdict(archive))))))
    require(len(raw) <= MAX_STATE_BYTES, "LIMIT", "archive byte ceiling")
    return raw


def decode_archive(raw: bytes, expected_root: str) -> Archive:
    require(len(raw) <= MAX_STATE_BYTES, "LIMIT", "archive byte ceiling")
    value = document(raw)
    fields(value, "previous_root epoch receipts")
    archive = Archive(
        text(value["previous_root"]),
        integer(value["epoch"]),
        tuple(_receipt(item) for item in array(value["receipts"], limit=4161)),
    )
    require(archive.root == expected_root, "STORAGE", "archive commitment mismatch")
    return archive
