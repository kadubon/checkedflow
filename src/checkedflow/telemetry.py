"""Bounded local observations; exporting is explicit and outside protected work.

These records describe local invocations, not authoritative state transitions. They may
be lost on overflow or restart and must never be used to recover or authorize an effect.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from queue import Empty, Queue
from time import monotonic_ns, time_ns
from typing import TYPE_CHECKING, TextIO

from checkedflow.core.values import Failure, integer, require

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer

OPERATIONS = frozenset({"worker.step", "effect.step", "service.observe"})
REASONS = frozenset(
    {
        "ACCESS",
        "AUTHORITY",
        "BUSY",
        "CAPACITY",
        "CLEANUP_UNKNOWN",
        "INTEGRITY",
        "NOT_READY",
        "OUTCOME_UNKNOWN",
        "POLICY",
        "STALE",
        "STOPPED",
        "UNAVAILABLE",
    }
)
MAX_BATCH = 256


@dataclass(frozen=True)
class Record:
    operation: str
    started_ms: int
    duration_us: int
    outcome: str
    reason: str

    def __post_init__(self) -> None:
        require(self.operation in OPERATIONS, "SHAPE", "telemetry operation")
        integer(self.started_ms)
        integer(self.duration_us)
        require(self.outcome in {"returned", "failed", "interrupted"}, "SHAPE", "telemetry outcome")
        require(self.reason in REASONS | {"NONE", "OTHER"}, "SHAPE", "telemetry reason")
        require(
            (self.outcome == "returned") == (self.reason == "NONE"), "SHAPE", "telemetry reason"
        )

    def json_line(self) -> str:
        import json

        return (
            json.dumps(
                {"profile": "checkedflow/operation-observation/v1", **asdict(self)},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )


class Recorder:
    """Opt-in finite queue, with no file writes, exporters, listeners or background tasks."""

    def __init__(
        self,
        capacity: int = MAX_BATCH,
        *,
        clock: Callable[[], int] = monotonic_ns,
        wall: Callable[[], int] = time_ns,
    ) -> None:
        self._queue: Queue[Record] = Queue(integer(capacity, low=1, high=MAX_BATCH))
        self._clock, self._wall = clock, wall

    @contextmanager
    def measure(self, operation: str) -> Iterator[None]:
        require(operation in OPERATIONS, "SHAPE", "telemetry operation")
        started = wall = None
        with suppress(Exception):
            started, wall = self._clock(), self._wall()
        outcome, reason = "returned", "NONE"
        try:
            yield
        except BaseException as error:
            outcome = "failed" if isinstance(error, Exception) else "interrupted"
            reason = error.code if isinstance(error, Failure) and error.code in REASONS else "OTHER"
            raise
        finally:
            # Best-effort telemetry cannot replace the original return or exception.
            with suppress(Exception):
                if started is not None and wall is not None:
                    ended = self._clock()
                    require(
                        type(started) is int
                        and type(wall) is int
                        and type(ended) is int
                        and 0 <= started <= ended
                        and wall >= 0,
                        "CLOCK",
                        "telemetry clock",
                    )
                    self._queue.put_nowait(
                        Record(
                            operation, wall // 1_000_000, (ended - started) // 1000, outcome, reason
                        )
                    )

    def drain(self, limit: int = MAX_BATCH) -> tuple[Record, ...]:
        integer(limit, low=1, high=MAX_BATCH)
        result = []
        for _ in range(limit):
            try:
                result.append(self._queue.get_nowait())
            except Empty:
                break
        return tuple(result)


def write_json(records: tuple[Record, ...], output: TextIO) -> None:
    """Explicit operator-side export. Use a protected, rotated file and bounded I/O."""
    require(len(records) <= MAX_BATCH, "LIMIT", "telemetry batch")
    for record in records:
        output.write(record.json_line())


def trace(records: tuple[Record, ...], tracer: "Tracer") -> None:
    """Export observed intervals through an explicitly supplied OpenTelemetry tracer.

    Run exporters in a separate operator process with resource/time limits. Never call
    this from consensus or a protected-work callback. No ambient/client trace context,
    exception text, identifiers, secrets or URLs are copied into spans.
    """
    from opentelemetry.context import Context
    from opentelemetry.trace import Status, StatusCode

    require(len(records) <= MAX_BATCH, "LIMIT", "telemetry batch")
    for record in records:
        start = record.started_ms * 1_000_000
        span = tracer.start_span(
            "checkedflow." + record.operation,
            context=Context(),
            start_time=start,
            attributes={
                "checkedflow.outcome": record.outcome,
                "checkedflow.reason": record.reason,
                "checkedflow.duration_us": record.duration_us,
            },
        )
        if record.outcome != "returned":
            span.set_status(Status(StatusCode.ERROR))
        span.end(end_time=start + record.duration_us * 1000)
