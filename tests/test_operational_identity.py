"""Explicit v2 identity purpose and signed-byte boundary, separate from v1 replay."""

from dataclasses import replace
from hashlib import sha256
from importlib.resources import files

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator

from checkedflow.core.values import Failure
from checkedflow.identity import sign
from checkedflow.operational_identity import Credential, authenticate, sign_command
from checkedflow.wire import dumps, loads


def setup():
    # Public deterministic fixture identities, never deployment key material.
    keys = {
        (name, 1): Ed25519PrivateKey.from_private_bytes(sha256(name.encode()).digest())
        for name in ("a", "b", "c", "d", "worker")
    }
    registry = {
        (name, revision): Credential(
            name,
            revision,
            "a" if name == "worker" else name,
            "executor" if name == "worker" else "administrator",
            "m" if name == "worker" else "",
            key.public_key().public_bytes_raw().hex(),
            0,
        )
        for (name, revision), key in keys.items()
    }
    command = {
        "api_version": "checkedflow/v2",
        "chain": "operational-test",
        "epoch": 3,
        "id": "request-1",
        "actor": "a",
        "revision": 1,
        "nonce": 1,
        "kind": "mission.pause",
        "payload": {"mission": "m"},
    }
    return keys, registry, command


def verify(raw, registry, **changes):
    return authenticate(
        raw,
        **dict(
            chain="operational-test",
            epoch=3,
            height=10,
            organizations=frozenset("abcd"),
            registry=registry,
        )
        | changes,
    )


def test_three_organizations_and_purposes_are_separate():
    keys, registry, command = setup()
    raw = sign_command(command, {k: v for k, v in keys.items() if k[0] in "abc"})
    decoded, verified = verify(raw, registry)
    assert decoded == command
    verified.require_administration()
    with pytest.raises(Failure, match="different signing purpose"):
        verified.require_role("executor", "m")
    registry[("b", 1)] = replace(registry[("b", 1)], organization="a")
    _, verified = verify(raw, registry)
    with pytest.raises(Failure, match="three administrative organizations"):
        verified.require_administration()


def test_worker_cannot_promote_its_role_or_mission():
    keys, registry, command = setup()
    command["actor"] = "worker"
    raw = sign_command(command, {("worker", 1): keys[("worker", 1)]})
    _, verified = verify(raw, registry)
    verified.require_role("executor", "m")
    with pytest.raises(Failure, match="administrative actor"):
        verified.require_administration()
    with pytest.raises(Failure, match="outside mission"):
        verified.require_role("executor", "other")
    with pytest.raises(Failure, match="different signing purpose"):
        verified.require_role("verifier", "m")


@pytest.mark.parametrize(
    "changes,code",
    [({"chain": "other"}, "CHAIN"), ({"epoch": 4}, "RETIRED_REQUEST"), ({"epoch": 2}, "EPOCH")],
)
def test_chain_epoch_admission_prevents_old_requests_becoming_new(changes, code):
    keys, registry, command = setup()
    with pytest.raises(Failure) as failure:
        verify(sign_command(command, {("a", 1): keys[("a", 1)]}), registry, **changes)
    assert failure.value.code == code


@pytest.mark.parametrize(
    "changes", [{"revoked": True}, {"activated_height": 11}, {"retired_height": 10}]
)
def test_height_and_revocation_reject_inactive_key(changes):
    keys, registry, command = setup()
    raw = sign_command(command, {("a", 1): keys[("a", 1)]})
    registry[("a", 1)] = replace(registry[("a", 1)], **changes)
    with pytest.raises(Failure, match="inactive or revoked"):
        verify(raw, registry)


def test_actor_revision_and_signed_bytes_are_binding():
    keys, registry, command = setup()
    raw = sign_command(command, {("a", 1): keys[("a", 1)]})
    envelope = loads(raw)
    envelope["command"]["nonce"] = 2
    with pytest.raises(Failure, match="invalid signature"):
        verify(dumps(envelope), registry)
    with pytest.raises(Failure, match="actor revision must sign"):
        verify(sign_command(command, {("b", 1): keys[("b", 1)]}), registry)
    envelope = loads(raw)
    envelope["signatures"][0]["revision"] = 2
    with pytest.raises(Failure, match="unknown identity revision"):
        verify(dumps(envelope), registry)
    registry[("a", 2)] = replace(registry[("a", 1)], revision=2)
    with pytest.raises(Failure, match="key reuse"):
        verify(raw, registry)


def test_duplicate_signatures_and_lexical_json_are_rejected():
    keys, registry, command = setup()
    raw = sign_command(command, {("a", 1): keys[("a", 1)]})
    with pytest.raises(Failure, match="original envelope bytes"):
        verify(loads(raw), registry)
    for damaged, code in [
        (raw.replace(b'"nonce":1', b'"nonce":1.0'), "NUMBER"),
        (raw.replace(b'"nonce":1', b'"nonce":1,"nonce":1'), "DUPLICATE_KEY"),
    ]:
        with pytest.raises(Failure) as failure:
            verify(damaged, registry)
        assert failure.value.code == code
    envelope = loads(raw)
    envelope["signatures"].append(envelope["signatures"][0])
    with pytest.raises(Failure, match="one revision per signer"):
        verify(dumps(envelope), registry)


def test_v1_signature_domain_cannot_authorize_v2():
    keys, registry, command = setup()
    legacy = sign(command, {"a": keys[("a", 1)]})
    legacy["signatures"][0]["revision"] = 1
    with pytest.raises(Failure, match="invalid signature"):
        verify(dumps(legacy), registry)
    command["api_version"] = "checkedflow/v1"
    with pytest.raises(Failure, match="v2 command required"):
        sign_command(command, keys)


def test_portable_envelope_schema_matches_valid_admission():
    schema = loads(
        files("checkedflow").joinpath("data/operational-envelope.schema.json").read_bytes()
    )
    validator = Draft202012Validator(schema)
    keys, registry, command = setup()
    raw = sign_command(command, {("a", 1): keys[("a", 1)]})
    envelope = loads(raw)
    validator.validate(envelope)
    verify(raw, registry)
    envelope["command"]["authority"] = True
    assert not validator.is_valid(envelope)
    with pytest.raises(Failure, match="unexpected or missing fields"):
        verify(dumps(envelope), registry)
