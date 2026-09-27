"""Provider-neutral artifact access, verified byte streams and storage protocol.

These service-side contracts perform I/O only when an adapter invokes them; consensus imports
only the portable Reference type from core.artifact.
"""

from dataclasses import dataclass
from hashlib import sha256
from typing import BinaryIO, Protocol

from checkedflow.core.artifact import Reference
from checkedflow.core.values import require, text


@dataclass(frozen=True)
class Access:
    """Trusted service-local policy result; never deserialize this from client input."""

    principal: str
    scopes: frozenset[str]
    permissions: frozenset[str]

    def authorize(self, scope: str, permission: str) -> None:
        text(self.principal, limit=80)
        require(
            scope in self.scopes and permission in self.permissions,
            "AUTHORITY",
            "artifact access denied",
        )


class ArtifactStore(Protocol):
    """Provider-neutral verified read and atomic publication interface."""

    def put(self, ref: Reference, source: BinaryIO, *, access: Access) -> None: ...

    def get(self, ref: Reference, *, access: Access) -> bytes: ...


class ErasableStore(ArtifactStore, Protocol):
    """Privileged provider operation; only a retention controller should receive erase authority."""

    def erase(self, ref: Reference, *, access: Access) -> None: ...


def verify(ref: Reference, body: bytes) -> None:
    require(len(body) == ref.length, "INTEGRITY", "artifact byte length differs")
    require(sha256(body).hexdigest() == ref.digest, "INTEGRITY", "artifact digest differs")


def read_verified(ref: Reference, source: BinaryIO) -> bytes:
    body = bytearray()
    while True:
        ceiling = min(65_536, ref.length - len(body) + 1)
        part = source.read(ceiling)
        require(isinstance(part, bytes), "SHAPE", "binary artifact stream required")
        require(len(part) <= ceiling, "LIMIT", "stream exceeded requested read bound")
        if not part:
            break
        body.extend(part)
        require(len(body) <= ref.length, "INTEGRITY", "artifact exceeds declared length")
    result = bytes(body)
    verify(ref, result)
    return result
