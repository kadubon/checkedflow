"""An authenticated runtime independent of transport and persistence."""

from copy import deepcopy

from checkedflow.core.machine import advance, transition
from checkedflow.core.model import State
from checkedflow.core.values import Object
from checkedflow.identity import authenticate
from checkedflow.serialization import encode
from checkedflow.wire import digest


class Runtime:
    """Own a committed snapshot. Network adapters control when a block commits."""

    def __init__(self, state: State) -> None:
        self._state = deepcopy(state)

    @property
    def state(self) -> State:
        return deepcopy(self._state)

    @property
    def state_hash(self) -> str:
        return digest(encode(self._state))

    def apply(self, envelope: Object, *, height: int) -> State:
        command, context = authenticate(self._state, envelope, height)
        self._state = transition(self._state, command, context)
        return self.state

    def tick(self, height: int) -> State:
        self._state = advance(self._state, height)
        return self.state
