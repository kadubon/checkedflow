"""Both administrations must approve the exact old root, new state and validator set."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator
from test_legacy_successor import source
from test_operational_runtime import runtime_and_command

from checkedflow.core.values import Failure
from checkedflow.legacy_inventory import Checkpoint
from checkedflow.legacy_successor import prepare
from checkedflow.operational_codec import decode
from checkedflow.succession import approve, authorize_startup, proposal, verify
from checkedflow.wire import digest, document, dumps


def test_packaged_approval_vector_and_schema():
    data = files("checkedflow").joinpath("data")
    vector = document(data.joinpath("succession-vector.json").read_bytes())
    legacy = document(data.joinpath("legacy-v1.json").read_bytes())
    schema = document(data.joinpath("succession.schema.json").read_bytes())
    Draft202012Validator(schema).validate(vector["manifest"])
    result = verify(
        dumps(vector["manifest"]),
        legacy=dumps(legacy["final_state"]),
        trusted=Checkpoint(**vector["checkpoint"]),
        successor=decode(dumps(vector["successor"])),
        validators=tuple(tuple(pair) for pair in vector["validators"]),
    )
    assert result.plan_hash == vector["plan_hash"]


def fixture(*, height=None):
    legacy, trusted = source()
    if height is not None:
        # Synthetic numeric boundary, not a new authentic historical capture.
        value = document(legacy)
        value["height"] = height
        legacy = dumps(value)
        trusted = replace(trusted, height=height, state_hash=digest(value))
    runtime, _, new_keys = runtime_and_command()
    successor = prepare(legacy, trusted, runtime.state, mission="m")
    validators = tuple(
        (org, sha256(("validator-" + org).encode()).hexdigest()) for org in successor.organizations
    )
    inputs = dict(legacy=legacy, trusted=trusted, successor=successor, validators=validators)
    plan = proposal(**inputs)
    approvals = []
    for index in range(3):
        identity = f"org{index}"
        # Existing published-v1 capture uses these explicitly public test identities.
        key = Ed25519PrivateKey.from_private_bytes(sha256(identity.encode()).digest())
        approvals.append(approve(plan, "old", identity, identity, key))
    for (identity, _), key in new_keys.items():
        approvals.append(approve(plan, "new", identity, identity, key))
    return inputs, {"plan": plan, "approvals": approvals}, new_keys


def test_both_quorums_bind_actual_published_checkpoint_and_prepared_state():
    inputs, envelope, _ = fixture()
    result = verify(dumps(envelope), **inputs)
    assert result.old_organizations == ("org0", "org1", "org2")
    assert result.new_organizations == ("a", "b", "c")
    shuffled = envelope | {"approvals": list(reversed(envelope["approvals"]))}
    assert verify(dumps(shuffled), **inputs) == result


def test_inherited_startup_cannot_omit_approval():
    inputs, _, _ = fixture()
    with pytest.raises(Failure, match="BINDING"):
        authorize_startup(inputs["successor"], inputs["validators"], None)


def test_boolean_height_cannot_alias_signed_integer():
    inputs, envelope, _ = fixture(height=1)
    envelope["plan"]["old"]["height"] = True
    with pytest.raises(Failure, match="BINDING"):
        verify(dumps(envelope), **inputs)


@pytest.mark.parametrize("side", ["old", "new"])
def test_one_quorum_cannot_replace_the_other(side):
    inputs, envelope, _ = fixture()
    approvals = envelope["approvals"]
    approvals.remove(next(row for row in approvals if row["side"] == side))
    with pytest.raises(Failure, match="QUORUM"):
        verify(dumps(envelope), **inputs)


@pytest.mark.parametrize(
    "damage",
    [
        "old_root",
        "new_root",
        "validators",
        "duplicate",
        "signature",
        "side",
        "old_worker",
        "new_worker",
        "extra",
        "malformed",
    ],
)
def test_tampering_and_role_substitution_fail_closed(damage):
    inputs, envelope, keys = fixture()
    if damage == "old_root":
        envelope["plan"]["old"]["state_hash"] = "0" * 64
    elif damage == "new_root":
        envelope["plan"]["new"]["state_hash"] = "0" * 64
    elif damage == "validators":
        envelope["plan"]["validators"][0]["public_key"] = "0" * 64
    elif damage == "duplicate":
        envelope["approvals"].append(envelope["approvals"][0])
    elif damage == "signature":
        envelope["approvals"][0]["signature"] = "0" * 128
    elif damage == "side":
        envelope["approvals"][0]["side"] = "candidate"
    elif damage == "old_worker":
        envelope["approvals"][0]["identity"] = "w0"
    elif damage == "new_worker":
        envelope["approvals"][3]["identity"] = "worker"
    elif damage == "extra":
        envelope["approvals"].append(
            approve(envelope["plan"], "old", "org3", "org3", keys[("a", 1)])
        )
    else:
        envelope["approvals"][0]["signature"] = "XX" * 64
    with pytest.raises(Failure):
        verify(dumps(envelope), **inputs)


def test_validator_keys_and_funding_are_independently_supplied():
    inputs, _, _ = fixture()
    with pytest.raises(Failure, match="GENESIS"):
        proposal(**(inputs | {"validators": inputs["validators"][:3]}))
    validators = list(inputs["validators"])
    validators[0] = (validators[0][0], inputs["successor"].credentials[0].public_key)
    with pytest.raises(Failure, match="GENESIS"):
        proposal(**(inputs | {"validators": tuple(validators)}))
    validators[0] = (validators[0][0], "invalid")
    with pytest.raises(Failure, match="GENESIS"):
        proposal(**(inputs | {"validators": tuple(validators)}))
    state = inputs["successor"]
    with pytest.raises(Failure, match="BINDING"):
        proposal(
            **(
                inputs
                | {"successor": replace(state, budget=replace(state.budget, inheritance=None))}
            )
        )
    with pytest.raises(Failure, match="BINDING"):
        proposal(**(inputs | {"successor": replace(state, mode="running")}))


def test_bounds_lexical_admission_and_signer_output():
    inputs, envelope, keys = fixture()
    with pytest.raises(Failure, match="LIMIT"):
        verify(b" " * 16385, **inputs)
    with pytest.raises(Failure, match="DUPLICATE_KEY"):
        verify(b'{"plan":{},"plan":{}}', **inputs)
    large = deepcopy(envelope["plan"])
    large["old"]["chain"] = "x" * 9000
    with pytest.raises(Failure, match="LIMIT"):
        approve(large, "old", "org0", "org0", keys[("a", 1)])

    class BrokenSigner:
        def sign(self, message):
            return b"invalid"

    with pytest.raises(Failure, match="SIGNATURE"):
        approve(envelope["plan"], "old", "org0", "org0", BrokenSigner())
