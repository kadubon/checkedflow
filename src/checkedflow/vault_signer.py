"""Vault Transit Ed25519 adapter. Service observations never enter pure transitions."""

import base64
import binascii
import http.client
import ipaddress
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from checkedflow.core.authority import Credential
from checkedflow.core.values import Failure, Object, integer, obj, require, text
from checkedflow.operational_identity import DOMAIN, POSSESSION_DOMAIN
from checkedflow.wire import MAX_TRANSACTION_BYTES, document, dumps

MAX_RESPONSE = 262_144


def _base64(value: str, length: int) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise Failure("SIGNER_BINDING", "invalid signing-service encoding") from None
    require(
        len(raw) == length and base64.b64encode(raw).decode("ascii") == value,
        "SIGNER_BINDING",
        "signing-service value length or encoding",
    )
    return raw


@dataclass(frozen=True)
class VaultSigner:
    """Bind a service key version to one already selected credential, not to 'latest'.

    Token issuance/renewal and provider administration belong to the operator. This object only
    reads public metadata and signs; no private export, create, configure or rotate endpoint exists.
    """

    credential: Credential
    endpoint: str
    key_name: str
    key_version: int
    token: str = field(repr=False)
    mount: str = "transit"
    timeout_seconds: int = 5
    ca_file: Path | None = None
    allow_insecure_loopback: bool = False

    def __post_init__(self) -> None:
        integer(self.key_version, low=1)
        integer(self.timeout_seconds, low=1, high=30)
        for value in (self.key_name, self.mount):
            text(value, limit=80)
            require(
                all(
                    c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                    for c in value
                ),
                "CONFIGURATION",
                "single portable Vault path segment required",
            )
        require(
            isinstance(self.token, str)
            and 0 < len(self.token) <= 4096
            and all(33 <= ord(c) <= 126 for c in self.token),
            "CONFIGURATION",
            "Vault token must be a bounded header value",
        )
        try:
            url = urlsplit(self.endpoint)
            valid_port = url.port is None or 1 <= url.port <= 65535
        except ValueError:
            raise Failure("CONFIGURATION", "invalid Vault endpoint") from None
        require(
            bool(url.hostname)
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
            and url.path in {"", "/"}
            and valid_port
            and url.scheme in {"http", "https"}
            and all(33 <= ord(c) <= 126 for c in self.endpoint),
            "CONFIGURATION",
            "Vault endpoint must be an operator-configured HTTP(S) origin",
        )
        if url.scheme == "http":
            try:
                loopback = ipaddress.ip_address(url.hostname or "").is_loopback
            except ValueError:
                loopback = False
            require(
                self.allow_insecure_loopback and loopback,
                "CONFIGURATION",
                "HTTPS required except explicit disposable loopback tests",
            )

    def _request(self, method: str, path: str, payload: Object | None = None) -> Object:
        url = urlsplit(self.endpoint)
        connection: http.client.HTTPConnection
        try:
            if url.scheme == "https":
                context = ssl.create_default_context(cafile=self.ca_file)
                connection = http.client.HTTPSConnection(
                    url.hostname or "", url.port, timeout=self.timeout_seconds, context=context
                )
            else:
                connection = http.client.HTTPConnection(
                    url.hostname or "", url.port, timeout=self.timeout_seconds
                )
            try:
                # No redirects or environment-derived proxy settings are used.
                connection.request(
                    method,
                    path,
                    body=None
                    if payload is None
                    else dumps(payload, string_limit=2 * MAX_TRANSACTION_BYTES),
                    headers={
                        "X-Vault-Token": self.token,
                        "Content-Type": "application/json",
                        "Accept-Encoding": "identity",
                    },
                )
                response = connection.getresponse()
                require(response.status != 403, "SIGNER_AUTH", "signing-service access denied")
                require(
                    response.status == 200, "SIGNER_UNAVAILABLE", "signing-service request failed"
                )
                require(
                    response.getheader("Content-Encoding") in {None, "identity"},
                    "SIGNER_RESPONSE",
                    "compressed signing-service responses are unsupported",
                )
                length = response.getheader("Content-Length")
                require(
                    length is None
                    or (
                        0 < len(length) <= 8
                        and all(c in "0123456789" for c in length)
                        and int(length) <= MAX_RESPONSE
                    ),
                    "SIGNER_RESPONSE",
                    "invalid signing-service content length",
                )
                body = response.read(MAX_RESPONSE + 1)
                require(
                    len(body) <= MAX_RESPONSE, "SIGNER_RESPONSE", "signing-service response ceiling"
                )
                require(
                    length is None or len(body) == int(length),
                    "SIGNER_RESPONSE",
                    "truncated signing-service response",
                )
            finally:
                connection.close()
        except (OSError, http.client.HTTPException):
            # Do not copy provider bodies, URLs, headers, tokens or OS paths into failure reports.
            raise Failure("SIGNER_UNAVAILABLE", "signing-service transport failed") from None
        try:
            return obj(document(body)["data"])
        except (Failure, KeyError):
            raise Failure("SIGNER_RESPONSE", "invalid signing-service response") from None

    def sign(self, message: bytes) -> bytes:
        require(
            isinstance(message, bytes)
            and len(message) <= MAX_TRANSACTION_BYTES
            and message.startswith((DOMAIN, POSSESSION_DOMAIN)),
            "SIGNATURE",
            "bounded v2 domain-separated message required",
        )
        metadata = self._request("GET", f"/v1/{self.mount}/keys/{self.key_name}")
        require(
            metadata.get("type") == "ed25519"
            and metadata.get("derived") is False
            and metadata.get("exportable") is False
            and metadata.get("allow_plaintext_backup") is False
            and metadata.get("supports_signing") is True,
            "SIGNER_BINDING",
            "non-exportable, non-derived Ed25519 key required",
        )
        try:
            version = obj(obj(metadata["keys"])[str(self.key_version)])
            public_key = _base64(text(version["public_key"]), 32)
        except (Failure, KeyError):
            raise Failure("SIGNER_BINDING", "pinned public key unavailable") from None
        require(
            public_key.hex() == self.credential.public_key,
            "SIGNER_BINDING",
            "provider key differs from credential",
        )
        result = self._request(
            "POST",
            f"/v1/{self.mount}/sign/{self.key_name}",
            {
                "input": base64.b64encode(message).decode("ascii"),
                "key_version": self.key_version,
                "prehashed": False,
            },
        )
        try:
            encoded = text(result["signature"]).split(":")
            require(
                len(encoded) == 3 and encoded[:2] == ["vault", f"v{self.key_version}"],
                "SIGNER_BINDING",
                "provider signature version differs",
            )
            signature = _base64(encoded[2], 64)
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        except (KeyError, InvalidSignature, Failure):
            raise Failure("SIGNER_BINDING", "provider returned an invalid signature") from None
        return signature
