"""Operator-owned authenticated encryption for private callback configurations."""

import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from checkedflow.core.values import Failure, Object, fields, obj, require, text
from checkedflow.wire import document, dumps

MAX_CONFIG_BYTES = 65536


class Keyring:
    """At most four operator-provisioned keys; key material never enters a journal."""

    def __init__(self, active: str, keys: dict[str, bytes]) -> None:
        require(0 < len(keys) <= 4 and active in keys, "SECRET_KEY", "invalid callback keyring")
        for identity, key in keys.items():
            require(
                0 < len(identity) <= 32
                and identity.isascii()
                and identity.isalnum()
                and isinstance(key, bytes)
                and len(key) == 32,
                "SECRET_KEY",
                "callback keys require an ASCII identifier and 32 bytes",
            )
        self.active, self._keys = active, dict(keys)

    @classmethod
    def ephemeral(cls) -> "Keyring":
        return cls("memory", {"memory": secrets.token_bytes(32)})

    @classmethod
    def load(cls, path: Path) -> "Keyring":
        try:
            with path.open("rb") as source:
                raw = source.read(4097)
            require(len(raw) <= 4096, "SECRET_KEY", "keyring size limit")
            value = document(raw)
            fields(value, "active keys")
            return cls(
                text(value["active"]),
                {
                    identity: bytes.fromhex(text(key, limit=64))
                    for identity, key in obj(value["keys"]).items()
                },
            )
        except (OSError, ValueError) as exc:
            raise Failure("SECRET_KEY", "callback keyring unavailable or invalid") from exc

    def seal(self, value: Object, binding: bytes) -> bytes:
        raw = dumps(value)
        require(len(raw) <= MAX_CONFIG_BYTES, "LIMIT", "callback configuration byte limit")
        nonce = secrets.token_bytes(12)
        encrypted = AESGCM(self._keys[self.active]).encrypt(nonce, raw, binding)
        return b"CFP1" + dumps({"key": self.active, "nonce": nonce.hex(), "data": encrypted.hex()})

    def open(self, raw: bytes, binding: bytes) -> Object:
        require(raw.startswith(b"CFP1"), "SECRET_FORMAT", "unsealed callback requires migration")
        require(len(raw) <= 2 * MAX_CONFIG_BYTES + 256, "LIMIT", "sealed configuration byte limit")
        try:
            value = document(raw[4:])
            fields(value, "key nonce data")
            key = self._keys[text(value["key"], limit=32)]
            nonce = bytes.fromhex(text(value["nonce"], limit=24))
            require(len(nonce) == 12, "SECRET_FORMAT", "invalid callback nonce")
            encrypted = bytes.fromhex(text(value["data"], limit=2 * MAX_CONFIG_BYTES + 32))
            return document(AESGCM(key).decrypt(nonce, encrypted, binding))
        except (InvalidTag, KeyError, ValueError) as exc:
            raise Failure(
                "SECRET_INTEGRITY", "callback configuration cannot be authenticated"
            ) from exc


def public_configuration(value: Object) -> Object:
    """Do not echo callback bearer credentials or notification tokens to a transport client."""
    result = dict(value)
    result.pop("token", None)
    result.pop("_principal", None)
    if "authentication" in result:
        authentication = dict(obj(result["authentication"]))
        authentication.pop("credentials", None)
        result["authentication"] = authentication
    return result
