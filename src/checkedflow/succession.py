"""Independent old/new administrative approval of an exact prepared successor.

Approval is not evidence that old dispatch stopped or that validator custody moved.
Trust anchors and the proposed validator set must come from protected operator inputs.
"""

from dataclasses import dataclass
from typing import cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from checkedflow.core.model import State as LegacyState
from checkedflow.core.operational import State, genesis
from checkedflow.core.values import Failure, Object, array, fields, obj, require, text
from checkedflow.core.work_budget import Inheritance
from checkedflow.legacy_inventory import Checkpoint, inspect_snapshot
from checkedflow.legacy_successor import prepare
from checkedflow.operational_codec import state_bytes
from checkedflow.operational_identity import Signer
from checkedflow.serialization import decode as decode_legacy
from checkedflow.wire import digest, document, dumps

DOMAIN = b"CheckedFlow/succession-approval/v1\x00"
PROFILE = "checkedflow/succession/v1"
MAX_BYTES = 16384


@dataclass(frozen=True)
class Approved:
    plan_hash: str
    old_organizations: tuple[str, ...]
    new_organizations: tuple[str, ...]


@dataclass(frozen=True)
class Succession:
    """Approval evidence plus an independently provisioned old checkpoint."""

    manifest: bytes
    legacy: bytes
    trusted: Checkpoint


def authorize_startup(
    initial: State, validators: tuple[tuple[str, str], ...], succession: Succession | None
) -> None:
    """Verify inherited genesis before a deployment creates state or listeners."""
    inherited = initial.budget.inheritance is not None
    require(inherited == (succession is not None), "BINDING", "successor approval required exactly")
    if succession is not None:
        verify(
            succession.manifest,
            legacy=succession.legacy,
            trusted=succession.trusted,
            successor=initial,
            validators=validators,
        )


def _inputs(
    legacy: bytes,
    trusted: Checkpoint,
    successor: State,
    validators: tuple[tuple[str, str], ...],
) -> tuple[LegacyState, Object]:
    inventory = inspect_snapshot(legacy, trusted)
    old = decode_legacy(document(inventory.snapshot))
    inherited = successor.budget.inheritance
    require(inherited is not None, "BINDING", "successor inheritance required")
    inherited = cast(Inheritance, inherited)
    fresh = genesis(
        successor.chain,
        successor.mission,
        successor.organizations,
        successor.credentials,
        limits=successor.journal.limits,
    )
    expected = prepare(legacy, trusted, fresh, mission=inherited.mission)
    require(successor == expected, "BINDING", "prepared successor differs from original funding")
    require(
        tuple(org for org, _ in validators) == successor.organizations,
        "GENESIS",
        "exact sorted validator organizations required",
    )
    keys = [key for _, key in validators]
    require(
        len(set(keys)) == 4
        and all(len(key) == 64 and all(char in "0123456789abcdef" for char in key) for key in keys),
        "GENESIS",
        "four distinct encoded validator keys required",
    )
    application_keys = set(old.organizations.values()) | {
        worker.key for worker in old.workers.values()
    }
    application_keys.update(credential.public_key for credential in successor.credentials)
    require(
        not set(keys) & application_keys, "GENESIS", "validator and application keys must differ"
    )
    plan: Object = {
        "profile": PROFILE,
        "old": {
            "chain": trusted.chain,
            "mission": inherited.mission,
            "height": trusted.height,
            "state_hash": trusted.state_hash,
        },
        "new": {
            "chain": successor.chain,
            "mission": successor.mission,
            "state_hash": digest(document(state_bytes(successor))),
        },
        "validators": [{"organization": org, "public_key": key} for org, key in validators],
    }
    return old, plan


def proposal(
    legacy: bytes, trusted: Checkpoint, successor: State, validators: tuple[tuple[str, str], ...]
) -> Object:
    """Construct a reviewable proposal; does not sign, deploy or grant authority."""
    return _inputs(legacy, trusted, successor, validators)[1]


def approval_message(plan: Object, side: str, organization: str, identity: str) -> bytes:
    fields(plan, "profile old new validators")
    require(plan["profile"] == PROFILE and side in {"old", "new"}, "VERSION", "approval purpose")
    body: Object = {
        "plan": plan,
        "side": side,
        "organization": text(organization, limit=80),
        "identity": text(identity, limit=80),
    }
    message = DOMAIN + dumps(body)
    require(len(message) <= MAX_BYTES // 2, "LIMIT", "approval message ceiling")
    return message


def approve(plan: Object, side: str, organization: str, identity: str, signer: Signer) -> Object:
    """Sign an explicitly reviewed proposal using the caller's protected signer.

    The caller must enforce its approval policy and conflicting-plan custody. This
    function does not automatically approve proposals supplied by agents or candidates.
    """
    signature = signer.sign(approval_message(plan, side, organization, identity))
    require(isinstance(signature, bytes) and len(signature) == 64, "SIGNATURE", "signature bytes")
    return {
        "side": side,
        "organization": organization,
        "identity": identity,
        "signature": signature.hex(),
    }


def verify(
    raw: bytes,
    *,
    legacy: bytes,
    trusted: Checkpoint,
    successor: State,
    validators: tuple[tuple[str, str], ...],
) -> Approved:
    """Require three distinct organizations on each side of the exact succession.

    Expected roots, successor and validators are independent inputs, never accepted
    merely because the submitted manifest names them. All supplied signatures must pass.
    """
    require(len(raw) <= MAX_BYTES, "LIMIT", "succession manifest byte ceiling")
    envelope = document(raw)
    fields(envelope, "plan approvals")
    old, expected = _inputs(legacy, trusted, successor, validators)
    require(
        dumps(obj(envelope["plan"])) == dumps(expected), "BINDING", "succession proposal differs"
    )
    accepted: dict[str, set[str]] = {"old": set(), "new": set()}
    for item in array(envelope["approvals"], limit=8):
        row = obj(item)
        fields(row, "side organization identity signature")
        side, organization, identity = (
            text(row["side"]),
            text(row["organization"]),
            text(row["identity"]),
        )
        message = approval_message(expected, side, organization, identity)
        require(organization not in accepted[side], "SIGNATURE", "duplicate organization approval")
        if side == "old":
            require(
                identity == organization and identity in old.organizations,
                "AUTHORITY",
                "old administrator required",
            )
            key = old.organizations[identity]
        else:
            credentials = [
                item
                for item in successor.credentials
                if item.identity == identity
                and item.organization == organization
                and item.role == "administrator"
                and item.usable_at(0)
            ]
            require(len(credentials) == 1, "AUTHORITY", "new administrator required")
            key = credentials[0].public_key
        signature = text(row["signature"])
        require(
            len(signature) == 128
            and len(key) == 64
            and all(char in "0123456789abcdef" for char in signature + key),
            "SIGNATURE",
            "canonical approval encoding required",
        )
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(
                bytes.fromhex(signature), message
            )
        except (InvalidSignature, ValueError) as error:
            raise Failure("SIGNATURE", "invalid succession approval") from error
        accepted[side].add(organization)
    require(
        all(len(organizations) >= 3 for organizations in accepted.values()),
        "QUORUM",
        "three old and three new organizations required",
    )
    return Approved(
        digest(expected), tuple(sorted(accepted["old"])), tuple(sorted(accepted["new"]))
    )
