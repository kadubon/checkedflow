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
from checkedflow.core.work_archive import MAX_ARCHIVE_BYTES, Head, WorkArchive
from checkedflow.core.work_budget import MAX_TICKETS, Inheritance, Ledger, LegacyObligation, Ticket
from checkedflow.core.work_budget import validate as validate_budget
from checkedflow.core.work_effects import MAX_EFFECTS, Effect
from checkedflow.core.work_effects import advance as advance_effects
from checkedflow.core.work_effects import validate as validate_effects
from checkedflow.core.work_tasks import MAX_TASKS, ROLE, Task
from checkedflow.core.work_tasks import advance as advance_tasks
from checkedflow.core.work_tasks import validate as validate_tasks
from checkedflow.wire import document, dumps, validate

MAX_STATE_BYTES = 4194304


def encode(state: State) -> Object:
    result = obj(validate(json.loads(json.dumps(asdict(state)))))
    if state.budget.inheritance is None:
        obj(result["budget"]).pop("inheritance")
    elif not state.budget.inheritance.obligations:
        obj(obj(result["budget"])["inheritance"]).pop("obligations")
    # Preserve historical v2 hashes before the first retirement, as well as all v1 bytes.
    if state.history == Head():
        result.pop("history")
    if not state.effects:
        result.pop("effects")
    if state.budget.archived_spent == state.budget.archived_verification == 0:
        budget = obj(result["budget"])
        budget.pop("archived_spent")
        budget.pop("archived_verification")
    return result


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
        "budget tasks candidates"
        + (" history" if "history" in value else "")
        + (" effects" if "effects" in value else ""),
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
    fields(
        budget_value,
        "budget tickets verification_reserve"
        + (
            " archived_spent archived_verification"
            if {"archived_spent", "archived_verification"}.intersection(budget_value)
            else ""
        )
        + (" inheritance" if "inheritance" in budget_value else ""),
    )
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
    inheritance = None
    if "inheritance" in budget_value:
        record = obj(budget_value["inheritance"])
        fields(
            record,
            "chain mission height state_hash budget spent reserved"
            + (" obligations" if "obligations" in record else ""),
        )
        obligations = []
        for liability_value in array(record.get("obligations", []), limit=MAX_TICKETS):
            liability_row = obj(liability_value)
            fields(liability_row, "identity task_hash ceiling funded outcome evidence charged")
            require(type(liability_row["funded"]) is bool, "STATE", "legacy funding flag")
            require(
                isinstance(liability_row["outcome"], str)
                and isinstance(liability_row["evidence"], str),
                "SHAPE",
                "legacy reconciliation strings",
            )
            obligations.append(
                LegacyObligation(
                    text(liability_row["identity"]),
                    text(liability_row["task_hash"]),
                    integer(liability_row["ceiling"], low=1),
                    cast(bool, liability_row["funded"]),
                    text(liability_row["outcome"]) if liability_row["outcome"] else "",
                    text(liability_row["evidence"]) if liability_row["evidence"] else "",
                    integer(liability_row["charged"]),
                )
            )
        inheritance = Inheritance(
            text(record["chain"], limit=128),
            text(record["mission"]),
            integer(record["height"]),
            text(record["state_hash"]),
            integer(record["budget"], low=1),
            integer(record["spent"]),
            integer(record["reserved"]),
            tuple(obligations),
        )
        require(inheritance.chain != initial.chain, "CHAIN", "successor must use a new chain")
    budget = Ledger(
        integer(budget_value["budget"]),
        tuple(tickets),
        integer(budget_value["verification_reserve"]),
        integer(budget_value.get("archived_spent", 0)),
        integer(budget_value.get("archived_verification", 0)),
        inheritance,
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
    effects = []
    for item in array(value.get("effects", []), limit=MAX_EFFECTS):
        effect = obj(item)
        fields(
            effect,
            "identity operation candidate ticket intent policy executor revision prepared "
            "expires lease_blocks status authorization authorized fence reserved "
            "until number evidence reason",
        )
        effects.append(
            Effect(
                text(effect["identity"], limit=80),
                text(effect["operation"]),
                text(effect["candidate"], limit=80),
                text(effect["ticket"], limit=80),
                text(effect["intent"]),
                text(effect["policy"]),
                text(effect["executor"], limit=80),
                integer(effect["revision"], low=1),
                integer(effect["prepared"]),
                integer(effect["expires"]),
                integer(effect["lease_blocks"]),
                text(effect["status"]),
                _string(effect["authorization"]),
                integer(effect["authorized"]),
                integer(effect["fence"]),
                integer(effect["reserved"]),
                integer(effect["until"]),
                integer(effect["number"]),
                _string(effect["evidence"]),
                _string(effect["reason"]),
            )
        )
    validate_effects(
        budget,
        tuple(effects),
        tuple(candidates),
        tuple(tasks),
        tuple(credentials),
        initial.mission,
        height,
    )
    require(
        advance_effects(budget, tuple(effects), tuple(candidates), tuple(credentials), height)
        == (budget, tuple(effects)),
        "STATE",
        "unapplied effect expiry or authority loss",
    )
    ordinary_ids = (
        {ticket.identity for ticket in budget.tickets}
        | {task.identity for task in tasks}
        | {candidate.identity for candidate in candidates}
        | {effect.identity for effect in effects}
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
    history = Head()
    if "history" in value:
        record = obj(value["history"])
        fields(record, "sequence root")
        history = Head(integer(record["sequence"], low=1), text(record["root"]))
    require(
        history.sequence > 0 or budget.archived_spent == 0,
        "STATE",
        "archived spending without archive commitment",
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
        history=history,
        effects=tuple(effects),
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


def work_archive_bytes(archive: WorkArchive) -> bytes:
    _ = archive.root
    return dumps(
        {
            "previous_root": archive.previous_root,
            "sequence": archive.sequence,
            "body": document(archive.body),
        }
    )


def decode_work_archive(raw: bytes, expected_root: str) -> WorkArchive:
    require(len(raw) <= MAX_ARCHIVE_BYTES + 256, "LIMIT", "work archive byte ceiling")
    value = document(raw)
    fields(value, "previous_root sequence body")
    body = obj(value["body"])
    fields(body, "chain mission height tickets tasks candidates")
    text(body["chain"], limit=128)
    text(body["mission"], limit=80)
    integer(body["height"], low=1)
    for key, limit in (("tickets", 128), ("tasks", 128), ("candidates", 64)):
        array(body[key], limit=limit)
    archive = WorkArchive(
        text(value["previous_root"]), integer(value["sequence"], low=1), dumps(body)
    )
    require(archive.root == expected_root, "STORAGE", "work archive commitment mismatch")
    require(work_archive_bytes(archive) == raw, "STATE", "noncanonical work archive")
    return archive
