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
    fields(value, "chain mission organizations credentials journal height mode profile")
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
        require(receipt.administrative, "STATE", "control profile contains ordinary work")
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
    state = replace(
        initial, credentials=tuple(credentials), journal=reconstructed, height=height, mode=mode
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
