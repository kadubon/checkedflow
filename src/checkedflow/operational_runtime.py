"""Authenticated in-memory v2 control runtime; no execution or durable-finality claim."""

from hashlib import sha256

from checkedflow.core.operational import State, advance, transition
from checkedflow.core.request_journal import Archive
from checkedflow.core.values import require
from checkedflow.core.work_archive import WorkArchive
from checkedflow.operational_codec import state_bytes
from checkedflow.operational_identity import authenticate


class Runtime:
    """Immutable snapshots. Persistent adapters must atomically retain returned archive batches."""

    def __init__(self, state: State) -> None:
        require(
            state.profile == "checkedflow/control-state/v2", "VERSION", "unsupported state profile"
        )
        self._state = state

    @property
    def state(self) -> State:
        return self._state

    @property
    def state_hash(self) -> str:
        return sha256(state_bytes(self._state)).hexdigest()

    def apply(self, raw: bytes, *, height: int) -> Archive | WorkArchive | None:
        state = self._state
        command, context = authenticate(
            raw,
            chain=state.chain,
            epoch=state.journal.epoch,
            height=height,
            organizations=frozenset(state.organizations),
            registry={(item.identity, item.revision): item for item in state.credentials},
        )
        successor, archive = transition(state, command, context)
        self._state = successor
        return archive

    def tick(self, height: int) -> None:
        self._state = advance(self._state, height)
