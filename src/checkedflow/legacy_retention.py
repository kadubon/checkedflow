"""Authenticate legacy replay between independent roots and pin its stored evidence.

Storage availability is a current observation, not consensus or future availability.
The operator must independently provision both genesis and final checkpoints.
"""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from checkedflow.artifact_io import Access
from checkedflow.core.artifact import Reference, reference
from checkedflow.core.values import Object, array, fields, integer, obj, require, text
from checkedflow.legacy_inventory import Checkpoint, inspect_snapshot
from checkedflow.recovery import replay_blocks
from checkedflow.retention import Pin, RetentionStore
from checkedflow.serialization import decode
from checkedflow.wire import document, dumps


@dataclass(frozen=True)
class Retained:
    snapshot: Reference
    history: tuple[Reference, ...]
    pin: Pin

    def record(self) -> Object:
        """Portable recovery handle, never an authorization or trust anchor."""
        return {
            "version": "checkedflow/legacy-retained/v1",
            "snapshot": self.snapshot.record(),
            "history": [ref.record() for ref in self.history],
            "pin": {
                "scope": self.pin.scope,
                "identity": self.pin.identity,
                "sequence": self.pin.sequence,
                "principal": self.pin.principal,
            },
        }


def decode_retained(raw: bytes) -> Retained:
    """Decode a bounded handle; callers must separately verify roots and live storage."""
    require(len(raw) <= 131072, "LIMIT", "legacy retention handle byte ceiling")
    value = document(raw)
    fields(value, "version snapshot history pin")
    require(value["version"] == "checkedflow/legacy-retained/v1", "VERSION", "retention handle")
    snapshot = reference(obj(value["snapshot"]))
    history = tuple(reference(obj(row)) for row in array(value["history"], limit=127))
    pin = obj(value["pin"])
    fields(pin, "scope identity sequence principal")
    result = Retained(
        snapshot,
        history,
        Pin(
            text(pin["scope"]),
            text(pin["identity"]),
            integer(pin["sequence"]),
            text(pin["principal"]),
        ),
    )
    require(
        bool(history)
        and snapshot.kind == "snapshot"
        and result.pin.scope == snapshot.scope
        and result.pin.identity == "legacy-" + snapshot.manifest
        and len({ref.digest for ref in (snapshot, *history)}) == 1 + len(history)
        and all(
            ref.kind == "archive"
            and ref.scope == snapshot.scope
            and ref.manifest == snapshot.manifest
            for ref in history
        ),
        "BINDING",
        "consistent distinct legacy references required",
    )
    return result


def authenticate_history(
    history: tuple[Reference, ...],
    initial: Checkpoint,
    final: Checkpoint,
    store: RetentionStore,
    *,
    access: Access,
) -> None:
    """Replay bounded ordered chunks from independently trusted genesis to final root."""
    integer(initial.height)
    integer(final.height, low=1)
    require(
        initial.chain == final.chain and initial.height == 0,
        "CHECKPOINT",
        "trusted genesis required",
    )
    require(0 < len(history) <= 127, "LIMIT", "bounded history archive set required")
    current = initial
    for ref in history:
        require(
            ref.kind == "archive" and ref.manifest == final.state_hash,
            "BINDING",
            "history checkpoint differs",
        )
        chunk = document(store.get(ref, access=access), string_limit=2097152)
        fields(chunk, "initial blocks")
        start = inspect_snapshot(dumps(obj(chunk["initial"])), current)
        blocks = array(chunk["blocks"], limit=4096)
        require(bool(blocks), "REPLAY", "empty history chunk")
        runtime = replay_blocks(decode(document(start.snapshot)), (obj(block) for block in blocks))
        current = Checkpoint(runtime.state.chain, runtime.state.height, runtime.state_hash)
        require(current.height <= final.height, "CHECKPOINT", "history exceeds final checkpoint")
    require(current == final, "CHECKPOINT", "history does not reach trusted final checkpoint")


def preserve(
    raw: bytes,
    trusted: Checkpoint,
    history: tuple[Reference, ...],
    store: RetentionStore,
    *,
    access: Access,
    initial: Checkpoint,
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
    authenticate_history(history, initial, trusted, store, access=access)
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
    verify(retained, trusted, store, access=access, initial=initial)
    return retained


def verify(
    retained: Retained,
    trusted: Checkpoint,
    store: RetentionStore,
    *,
    access: Access,
    initial: Checkpoint,
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
    authenticate_history(retained.history, initial, trusted, store, access=access)
