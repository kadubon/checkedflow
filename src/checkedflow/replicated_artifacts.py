"""Fresh verified reads across four operator-configured artifact backends.

Replica labels are protected configuration, not proof of independent organizations or disks.
This service adapter never changes consensus acceptance and does not perform background repair.
"""

import re
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO, cast

from checkedflow.artifact_io import Access, ArtifactStore, read_verified, verify
from checkedflow.core.artifact import Reference
from checkedflow.core.values import Object, require


@dataclass(frozen=True)
class Replica:
    """One named, authenticated backend provisioned outside candidate/client control."""

    name: str
    store: ArtifactStore


@dataclass(frozen=True)
class Availability:
    """A bounded local observation; no future availability or consensus authority implied."""

    reference: Reference
    verified: tuple[str, ...]
    unavailable: tuple[str, ...]

    def __post_init__(self) -> None:
        names = self.verified + self.unavailable
        require(
            len(names) == len(set(names)) == 4
            and all(re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", name) for name in names)
            and tuple(sorted(self.verified)) == self.verified
            and tuple(sorted(self.unavailable)) == self.unavailable,
            "SHAPE",
            "availability requires a disjoint sorted partition of four replica names",
        )

    @property
    def usable(self) -> bool:
        return len(self.verified) >= 3

    def record(self) -> Object:
        return {
            "profile": "checkedflow/artifact-availability/v1",
            "reference": self.reference.record(),
            "required": 3,
            "verified": list(self.verified),
            "unavailable": list(self.unavailable),
        }


class ReplicatedStore:
    """Require three currently readable copies; probe all four once per operation.

    Backends must impose finite I/O deadlines. Their independent ownership, credentials,
    endpoints and failure domains require operator qualification, not inferred names.
    """

    def __init__(self, replicas: tuple[Replica, ...]) -> None:
        require(len(replicas) == 4, "SHAPE", "exactly four artifact replicas required")
        names = tuple(replica.name for replica in replicas)
        require(
            all(re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", name) for name in names)
            and tuple(sorted(set(names))) == names,
            "SHAPE",
            "unique sorted bounded replica names required",
        )
        require(
            len({id(replica.store) for replica in replicas}) == 4,
            "BINDING",
            "one backend instance cannot count as multiple replicas",
        )
        self.replicas = tuple(replicas)

    def _read(self, ref: Reference, access: Access) -> tuple[Availability, bytes | None]:
        access.authorize(ref.scope, "read")
        verified, unavailable = [], []
        result = None
        for replica in self.replicas:
            try:
                body = replica.store.get(ref, access=access)
                require(type(body) is bytes, "INTEGRITY", "replica returned nonbinary content")
                verify(ref, body)
            except Exception:
                # A backend failure is not an absence proof or permission to repair/erase.
                # Never expose raw provider exceptions, endpoints or credentials in receipts.
                unavailable.append(replica.name)
            else:
                verified.append(replica.name)
                result = body
        return Availability(ref, tuple(verified), tuple(unavailable)), result

    def inspect(self, ref: Reference, *, access: Access) -> Availability:
        """Fresh read-only observation; do not cache it as authorization for later use."""
        return self._read(ref, access)[0]

    def get(self, ref: Reference, *, access: Access) -> bytes:
        observed, body = self._read(ref, access)
        require(observed.usable and body is not None, "UNAVAILABLE", "artifact replica threshold")
        return cast(bytes, body)

    def put(self, ref: Reference, source: BinaryIO, *, access: Access) -> None:
        """Bounded publication plus readback; partial copies remain on failure, never erased."""
        access.authorize(ref.scope, "write")
        access.authorize(ref.scope, "read")
        body = read_verified(ref, source)
        for replica in self.replicas:
            try:
                replica.store.put(ref, BytesIO(body), access=access)
            except Exception:  # nosec B112: a fresh verified readback below decides publication
                # A lost acknowledgement can still have published complete immutable bytes.
                # Readback below decides availability; this call does not retry the write.
                continue
        self.get(ref, access=access)
