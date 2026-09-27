"""Pin authenticated legacy snapshot and operator-verified history archive references.

Storage availability is an observation, not consensus or proof of archive completeness.
The protected operator must authenticate history coverage independently before cutover.
"""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from checkedflow.artifact_io import Access
from checkedflow.core.artifact import Reference
from checkedflow.core.values import require
from checkedflow.legacy_inventory import Checkpoint, inspect_snapshot
from checkedflow.retention import Pin, RetentionStore


@dataclass(frozen=True)
class Retained:
    snapshot: Reference
    history: tuple[Reference, ...]
    pin: Pin


def preserve(
    raw: bytes,
    trusted: Checkpoint,
    history: tuple[Reference, ...],
    store: RetentionStore,
    *,
    access: Access,
) -> Retained:
    """Authenticate the snapshot, read history bytes, then pin the whole bounded set."""
    inventory = inspect_snapshot(raw, trusted)
    require(0 < len(history) <= 127, "LIMIT", "one to 127 history archive references required")
    require(
        len({ref.digest for ref in history}) == len(history), "BINDING", "distinct history archives"
    )
    for ref in history:
        require(
            ref.kind == "archive"
            and ref.scope == store.scope
            and ref.manifest == trusted.state_hash,
            "BINDING",
            "history must bind the legacy checkpoint and retention scope",
        )
        store.get(ref, access=access)
    snapshot = Reference(
        "sha256",
        sha256(inventory.snapshot).hexdigest(),
        len(inventory.snapshot),
        "application/json",
        "snapshot",
        store.scope,
        trusted.state_hash,
    )
    require(
        snapshot.digest not in {ref.digest for ref in history}, "BINDING", "snapshot is not history"
    )
    store.put(snapshot, BytesIO(inventory.snapshot), access=access)
    pin = store.pin(
        "legacy-" + trusted.state_hash, (snapshot, *history), category="replay", access=access
    )
    retained = Retained(snapshot, history, pin)
    verify(retained, trusted, store, access=access)
    return retained


def verify(
    retained: Retained, trusted: Checkpoint, store: RetentionStore, *, access: Access
) -> None:
    """Check retained bytes and an existing durable pin; never silently repin."""
    require(
        retained.snapshot.kind == "snapshot"
        and retained.snapshot.manifest == trusted.state_hash
        and retained.pin.identity == "legacy-" + trusted.state_hash,
        "BINDING",
        "retained legacy checkpoint differs",
    )
    require(0 < len(retained.history) <= 127, "LIMIT", "history archive references required")
    require(
        len({ref.digest for ref in retained.history}) == len(retained.history),
        "BINDING",
        "distinct history archives",
    )
    for ref in retained.history:
        require(
            ref.kind == "archive" and ref.manifest == trusted.state_hash,
            "BINDING",
            "history checkpoint differs",
        )
    store.verify_pin(retained.pin, (retained.snapshot, *retained.history), access=access)
    inspect_snapshot(store.get(retained.snapshot, access=access), trusted)
