"""Version-2 signed admission with explicit key purpose, revision and mission scope.

The registry is trusted committed state supplied by the runtime, never a request certificate.
Successful authentication is not execution permission or evidence acceptance.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from checkedflow.core.values import (
    JSON,
    Failure,
    Object,
    array,
    fields,
    integer,
    obj,
    require,
    text,
)
from checkedflow.wire import MAX_TRANSACTION_BYTES, digest, dumps, transaction_document

DOMAIN = b"CheckedFlow/command/v2\x00"
ROLES = frozenset({"administrator", "producer", "executor", "verifier", "effect_executor"})


class Signer(Protocol):
    """An adapter signs exact bytes without exposing its private key to this interface."""

    def sign(self, message: bytes) -> bytes: ...


@dataclass(frozen=True)
class Credential:
    identity: str
    revision: int
    organization: str
    role: str
    mission: str
    public_key: str
    activated_height: int
    retired_height: int | None = None
    revoked: bool = False

    def __post_init__(self) -> None:
        text(self.identity, limit=80)
        integer(self.revision, low=1)
        text(self.organization, limit=80)
        require(self.role in ROLES, "AUTHORITY", "unsupported signing purpose")
        require(
            (self.role == "administrator" and self.mission == "")
            or (self.role != "administrator" and 0 < len(self.mission) <= 80),
            "SCOPE",
            "non-administrative keys must bind one mission",
        )
        require(
            len(self.public_key) == 64 and all(c in "0123456789abcdef" for c in self.public_key),
            "SIGNATURE",
            "canonical Ed25519 public key required",
        )
        integer(self.activated_height)
        if self.retired_height is not None:
            integer(self.retired_height, low=self.activated_height + 1)
        require(type(self.revoked) is bool, "SHAPE", "boolean revocation required")

    def usable_at(self, height: int) -> bool:
        return (
            not self.revoked
            and self.activated_height <= height
            and (self.retired_height is None or height < self.retired_height)
        )


@dataclass(frozen=True)
class Verified:
    """Authenticated principals only; command semantics still require a transition."""

    command_digest: str
    actor: Credential
    signers: tuple[Credential, ...]
    height: int
    epoch: int

    def require_administration(self) -> None:
        require(self.actor.role == "administrator", "AUTHORITY", "administrative actor required")
        organizations = {
            credential.organization
            for credential in self.signers
            if credential.role == "administrator"
        }
        require(len(organizations) >= 3, "QUORUM", "three administrative organizations required")

    def require_role(self, role: str, mission: str) -> None:
        require(role in ROLES and role != "administrator", "AUTHORITY", "worker purpose required")
        require(self.actor.role == role, "AUTHORITY", "actor has a different signing purpose")
        require(self.actor.mission == mission, "SCOPE", "actor is outside mission")


def command_message(command: Object) -> bytes:
    fields(command, "api_version chain epoch id actor revision nonce kind payload")
    require(command["api_version"] == "checkedflow/v2", "VERSION", "v2 command required")
    text(command["chain"], limit=128)
    integer(command["epoch"])
    text(command["id"], limit=80)
    text(command["actor"], limit=80)
    integer(command["revision"], low=1)
    integer(command["nonce"], low=1)
    text(command["kind"], limit=80)
    obj(command["payload"])
    return DOMAIN + dumps(command)


def sign_command(command: Object, signers: Mapping[tuple[str, int], Signer]) -> bytes:
    """Produce a portable envelope; local and managed signers implement the same interface."""
    message = command_message(command)
    require(0 < len(signers) <= 8, "SIGNATURE", "one to eight signatures required")
    records: list[JSON] = []
    for (identity, revision), signer in sorted(signers.items()):
        text(identity, limit=80)
        integer(revision, low=1)
        signature = signer.sign(message)
        require(
            isinstance(signature, bytes) and len(signature) == 64,
            "SIGNATURE",
            "signer must return exactly 64 bytes",
        )
        records.append({"signer": identity, "revision": revision, "signature": signature.hex()})
    raw = dumps({"command": command, "signatures": records})
    require(len(raw) <= MAX_TRANSACTION_BYTES, "LIMIT", "transaction byte ceiling exceeded")
    return raw


def authenticate(
    raw: bytes,
    *,
    chain: str,
    epoch: int,
    height: int,
    organizations: frozenset[str],
    registry: Mapping[tuple[str, int], Credential],
) -> tuple[Object, Verified]:
    """Preserve lexical JSON until admission; never infer trust from enclosed public keys."""
    require(isinstance(raw, bytes), "SHAPE", "original envelope bytes required")
    integer(epoch)
    integer(height)
    require(len(organizations) == 4, "GENESIS", "four organizations required")
    require(len(registry) <= 1024, "LIMIT", "authentication registry bound exceeded")
    require(
        len({credential.public_key for credential in registry.values()}) == len(registry),
        "SIGNATURE",
        "key reuse across identities or revisions is prohibited",
    )
    envelope = transaction_document(raw)
    fields(envelope, "command signatures")
    command = obj(envelope["command"])
    message = command_message(command)
    require(command["chain"] == chain, "CHAIN", "wrong chain")
    command_epoch = integer(command["epoch"])
    require(command_epoch >= epoch, "RETIRED_REQUEST", "request epoch retired")
    require(command_epoch == epoch, "EPOCH", "future epoch")
    entries = array(envelope["signatures"], limit=8)
    require(bool(entries), "SIGNATURE", "signature required")
    seen: set[str] = set()
    seen_keys: set[str] = set()
    verified: list[Credential] = []
    actor = None
    for value in entries:
        record = obj(value)
        fields(record, "signer revision signature")
        identity = text(record["signer"], limit=80)
        revision = integer(record["revision"], low=1)
        require(identity not in seen, "SIGNATURE", "one revision per signer required")
        seen.add(identity)
        credential = registry.get((identity, revision))
        if credential is None:
            raise Failure("SIGNATURE", "unknown identity revision")
        require(
            credential.identity == identity and credential.revision == revision,
            "SIGNATURE",
            "registry identity mismatch",
        )
        require(credential.organization in organizations, "AUTHORITY", "organization not a member")
        require(credential.usable_at(height), "SIGNATURE", "inactive or revoked key revision")
        require(credential.public_key not in seen_keys, "SIGNATURE", "shared signer key")
        seen_keys.add(credential.public_key)
        signature = text(record["signature"])
        require(
            len(signature) == 128 and all(c in "0123456789abcdef" for c in signature),
            "SIGNATURE",
            "canonical signature required",
        )
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(credential.public_key)).verify(
                bytes.fromhex(signature), message
            )
        except InvalidSignature as exc:
            raise Failure("SIGNATURE", "invalid signature") from exc
        verified.append(credential)
        if identity == command["actor"] and revision == command["revision"]:
            actor = credential
    if actor is None:
        raise Failure("SIGNATURE", "actor revision must sign")
    return command, Verified(digest(command), actor, tuple(verified), height, epoch)
