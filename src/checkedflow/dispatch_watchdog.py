"""Local inhibition of dispatch; observations never confer execution authority.

This adapter uses process-local monotonic time, outside deterministic consensus. It does not
cancel an in-flight effect, extend a lease, authenticate a snapshot or make a stale node current.
"""

from collections.abc import Callable
from threading import Lock
from time import monotonic_ns

from checkedflow.core.operational import State
from checkedflow.core.values import Failure, integer, require, text


class Watchdog:
    """Require recent successful reads and observed committed-height progress.

    Supply a trusted own-node reader, such as Client.live_state. No polling thread is started.
    Startup and every interrupted observation sequence require two progressing running samples.
    The caller must independently check current task, fence, budget and effect authority.
    """

    def __init__(
        self,
        read: Callable[[], State],
        *,
        chain: str,
        mission: str,
        max_read_age_ns: int,
        max_stall_ns: int,
        clock: Callable[[], int] = monotonic_ns,
    ) -> None:
        self.chain, self.mission = text(chain, limit=128), text(mission, limit=80)
        self.max_age = integer(max_read_age_ns, low=1, high=3_600_000_000_000)
        self.max_stall = integer(max_stall_ns, low=1, high=3_600_000_000_000)
        self._read, self._clock = read, clock
        self._lock = Lock()
        self._stopped = False
        self._polling = False
        self._time = 0
        self._last: State | None = None
        self._state: State | None = None
        self._warm_height: int | None = None
        self._progress: int | None = None
        self._sample = 0

    def _invalidate(self) -> None:
        self._state = None
        self._warm_height = None
        self._progress = None

    def _now(self) -> int:
        now = self._clock()
        if type(now) is not int or now < self._time:
            self._stopped = True
            self._invalidate()
            require(False, "CLOCK", "monotonic clock invalid; watchdog latched stopped")
        self._time = now
        return now

    def _expire(self, now: int) -> None:
        if now - self._sample >= self.max_age or (
            self._progress is not None and now - self._progress >= self.max_stall
        ):
            self._invalidate()

    def stop(self) -> None:
        """Latch this instance off without waiting for an outstanding node read.

        Stop cannot revoke consensus authority or cancel already dispatched work. There is no
        automatic resume: a replacement supervisor must reestablish policy and cold-start.
        """
        with self._lock:
            self._stopped = True
            self._invalidate()

    def poll(self) -> State:
        """Read once; exceptions invalidate readiness before propagating to the supervisor."""
        with self._lock:
            require(not self._stopped, "STOPPED", "dispatch watchdog stopped")
            require(not self._polling, "BUSY", "one own-node read at a time")
            started = self._now()
            self._expire(started)
            self._polling = True
            self._state = None
        succeeded = False
        try:
            state = self._read()
            with self._lock:
                ended = self._now()
                require(not self._stopped, "STOPPED", "dispatch watchdog stopped")
                require(
                    state.chain == self.chain and state.mission == self.mission,
                    "SCOPE",
                    "own-node observation belongs to another chain or mission",
                )
                require(
                    state.profile == "checkedflow/control-state/v2",
                    "VERSION",
                    "own-node state profile differs",
                )
                integer(state.height)
                if self._last is not None and (
                    state.height < self._last.height
                    or (state.height == self._last.height and state != self._last)
                ):
                    self._stopped = True
                    require(False, "CONFLICT", "own-node rollback or inconsistent committed state")
                self._last = state
                require(ended - started < self.max_age, "STALE", "own-node read took too long")
                self._expire(ended)
                if state.mode != "running":
                    self._invalidate()
                else:
                    if self._warm_height is not None and state.height > self._warm_height:
                        self._progress = started
                    self._warm_height = state.height
                    self._sample = started
                    self._state = state
                succeeded = True
                return state
        finally:
            with self._lock:
                self._polling = False
                if not succeeded:
                    self._invalidate()

    def current(self) -> State:
        """Return a locally fresh observation, not a reusable dispatch permit."""
        with self._lock:
            require(not self._stopped, "STOPPED", "dispatch watchdog stopped")
            self._expire(self._now())
            if self._polling or self._state is None or self._progress is None:
                raise Failure(
                    "NOT_READY",
                    "recent running observations and committed-height progress required",
                )
            return self._state
