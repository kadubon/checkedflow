"""Ed25519 signatures bind command bytes to independently registered identities."""

from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from checkedflow.contracts import check
from checkedflow.core.model import Context, State
from checkedflow.core.values import Failure, Object, array, fields, obj, require, text
from checkedflow.wire import MAX_TRANSACTION_BYTES, digest, dumps

DOMAIN = b"CheckedFlow/command/v1\x00"


def public_key(private: Ed25519PrivateKey) -> str:
    return private.public_key().public_bytes_raw().hex()


def sign(command: Object, keys: Mapping[str, Ed25519PrivateKey]) -> Object:
    message = DOMAIN + dumps(command)
    return {
        "command": command,
        "signatures": [
            {"signer": signer, "signature": key.sign(message).hex()}
            for signer, key in sorted(keys.items())
        ],
    }


def authenticate(state: State, envelope: Object, height: int) -> tuple[Object, Context]:
    require(len(dumps(envelope)) <= MAX_TRANSACTION_BYTES, "LIMIT", "transaction byte limit")
    check(envelope)
    fields(envelope, "command signatures")
    command = obj(envelope["command"])
    message = DOMAIN + dumps(command)
    signatures = array(envelope["signatures"], limit=8)
    require(bool(signatures), "SIGNATURE", "signature required")
    signers: set[str] = set()
    for raw in signatures:
        record = obj(raw)
        fields(record, "signer signature")
        signer, signature = text(record["signer"]), text(record["signature"])
        require(signer not in signers, "SIGNATURE", "duplicate signer")
        if signer in state.organizations:
            key = state.organizations[signer]
        else:
            require(
                signer in state.workers and not state.workers[signer].revoked,
                "SIGNATURE",
                "unknown or revoked signer",
            )
            key = state.workers[signer].key
        try:
            require(len(signature) == 128, "SIGNATURE", "invalid signature size")
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(
                bytes.fromhex(signature), message
            )
        except (ValueError, InvalidSignature) as exc:
            raise Failure("SIGNATURE", "invalid signature") from exc
        signers.add(signer)
    return command, Context(height, frozenset(signers), digest(command))
