"""Finite persistent scheduling outside consensus; no new authority or execution retry."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from secrets import randbelow
from threading import Event
from time import monotonic_ns

from checkedflow.core.values import Failure, integer, names, require, text
from checkedflow.wire import dumps, validate
from checkedflow.worker_supervisor import Supervisor

RETRYABLE = frozenset({"BUSY", "NOT_READY", "STALE", "OUTCOME_UNKNOWN", "PAUSED", "CAPACITY"})
TERMINAL = frozenset({"finished", "unknown", "cancelled"})


@dataclass(frozen=True)
class Policy:
    """Immutable per-plan limits. Attempts include reconciliation and failed observations."""

    tasks: tuple[str, ...]
    attempts_per_task: int = 8
    total_attempts: int = 128
    duration_seconds: int = 300
    base_delay_ms: int = 250
    max_delay_ms: int = 10000

    def __post_init__(self) -> None:
        object.__setattr__(self, "tasks", names(list(self.tasks), limit=128))
        require(bool(self.tasks), "SHAPE", "nonempty task schedule required")
        integer(self.attempts_per_task, low=1, high=32)
        integer(self.total_attempts, low=1, high=4096)
        integer(self.duration_seconds, low=1, high=3600)
        integer(self.base_delay_ms, low=1, high=60000)
        integer(self.max_delay_ms, low=self.base_delay_ms, high=60000)


@dataclass(frozen=True)
class Status:
    """Local scheduling observation, never a consensus verdict or execution permit."""

    mode: str
    attempts: int
    remaining: int
    wait_ns: int


class Schedule:
    """One private immutable plan per directory, bound to a supervisor and clock epoch.

    The operator must supply an actual boot/session identity for the monotonic clock domain.
    A reboot or policy change cannot resume this plan by resetting its deadline. Callbacks used
    by Supervisor must have their own finite I/O/execution limits: this scheduler cannot forcibly
    cancel them. It checks its admission deadline before each invocation.
    """

    def __init__(
        self,
        directory: Path,
        supervisor: Supervisor,
        policy: Policy,
        *,
        clock_epoch: str,
        clock: Callable[[], int] = monotonic_ns,
        jitter: Callable[[int], int] = randbelow,
    ) -> None:
        self.directory, self.supervisor, self.policy = directory, supervisor, policy
        self._clock, self._jitter = clock, jitter
        self._stop = Event()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        coordinator = supervisor.coordinator
        binding = dumps(
            {
                "version": "checkedflow/worker-schedule/v1",
                "origin": coordinator.origin(),
                "actor": coordinator.actor,
                "worker_directory": str(supervisor.directory.resolve()),
                "clock_epoch": text(clock_epoch, limit=128),
                "policy": validate(asdict(policy) | {"tasks": list(policy.tasks)}),
            }
        )
        with self._db() as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS schedule (id INTEGER PRIMARY KEY CHECK(id=1), "
                "binding BLOB NOT NULL, deadline INTEGER NOT NULL, observed INTEGER NOT NULL, "
                "attempts INTEGER NOT NULL, stopped INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS queue (task TEXT PRIMARY KEY, "
                "position INTEGER NOT NULL, "
                "attempts INTEGER NOT NULL, due INTEGER NOT NULL, status TEXT NOT NULL, "
                "reason TEXT NOT NULL)"
            )
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT binding FROM schedule WHERE id=1").fetchone()
            if row is None:
                now = self._now()
                db.execute(
                    "INSERT INTO schedule VALUES (1, ?, ?, ?, 0, 0)",
                    (binding, now + policy.duration_seconds * 1000000000, now),
                )
                db.executemany(
                    "INSERT INTO queue VALUES (?, ?, 0, ?, 'pending', '')",
                    [(task, index, now) for index, task in enumerate(policy.tasks)],
                )
            else:
                require(row[0] == binding, "SCOPE", "schedule identity, clock or policy differs")

    def _db(self) -> closing[sqlite3.Connection]:
        db = sqlite3.connect(self.directory / "schedule.sqlite", timeout=0)
        db.execute("PRAGMA synchronous=FULL")
        return closing(db)

    def _now(self) -> int:
        # Monotonic nanoseconds are local SQLite integers, not JSON protocol numbers.
        now = self._clock()
        require(type(now) is int and 0 <= now <= 2**63 - 3600000000001, "CLOCK", "clock invalid")
        return now

    def stop(self) -> None:
        """Inhibit locally now and durably latch off; cannot cancel an in-flight operation."""
        self._stop.set()
        self.supervisor.watchdog.stop()
        with self._db() as db, db:
            db.execute("UPDATE schedule SET stopped=1 WHERE id=1")

    def status(self) -> Status:
        """Read finite plan state without contacting a node or granting readiness."""
        with self._db() as db, db:
            db.execute("BEGIN IMMEDIATE")
            deadline, observed, attempts, stopped = db.execute(
                "SELECT deadline, observed, attempts, stopped FROM schedule WHERE id=1"
            ).fetchone()
            try:
                now = self._now()
            except Failure:
                db.execute("UPDATE schedule SET stopped=1 WHERE id=1")
                db.commit()
                raise
            if now < observed:
                db.execute("UPDATE schedule SET stopped=1 WHERE id=1")
                db.commit()
                raise Failure("CLOCK", "clock rollback; schedule stopped")
            db.execute("UPDATE schedule SET observed=? WHERE id=1", (now,))
            pending = db.execute(
                "SELECT due FROM queue WHERE status IN ('pending', 'inflight')"
            ).fetchall()
            blocked = db.execute(
                "SELECT COUNT(*) FROM queue WHERE status IN ('blocked', 'unknown')"
            ).fetchone()[0]
            mode = "waiting"
            if stopped or self._stop.is_set():
                mode = "stopped"
            elif not pending:
                mode = "blocked" if blocked else "complete"
            elif now >= deadline or attempts >= self.policy.total_attempts:
                mode = "exhausted"
            wait = min((max(0, row[0] - now) for row in pending), default=0)
            return Status(mode, attempts, len(pending), min(wait, max(0, deadline - now)))

    def tick(self) -> Status:
        """At most one Supervisor.step; never retransmit, mint tasks, or reset execution state."""
        with closing(sqlite3.connect(self.directory / "schedule-lock.sqlite", timeout=0)) as lock:
            try:
                lock.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise Failure("BUSY", "schedule already running") from exc
            before = self.status()
            if before.mode != "waiting" or before.wait_ns:
                return before
            with self._db() as db, db:
                db.execute("BEGIN IMMEDIATE")
                now = self._now()
                deadline, stopped = db.execute(
                    "SELECT deadline, stopped FROM schedule WHERE id=1"
                ).fetchone()
                if stopped or now >= deadline:
                    db.commit()
                    return self.status()
                row = db.execute(
                    "SELECT task, attempts FROM queue WHERE status IN ('pending', 'inflight') "
                    "AND due<=? ORDER BY due, position LIMIT 1",
                    (now,),
                ).fetchone()
                task, attempts = row
                if attempts >= self.policy.attempts_per_task:
                    db.execute(
                        "UPDATE queue SET status='blocked', reason='LIMIT' WHERE task=?", (task,)
                    )
                    db.commit()
                    return self.status()
                # Commit the budget before any own-node read, signer call or execution.
                ceiling = min(self.policy.max_delay_ms, self.policy.base_delay_ms * 2**attempts)
                floor = max(1, ceiling // 2)
                offset = integer(self._jitter(ceiling - floor + 1), high=ceiling - floor)
                delay = (floor + offset) * 1000000
                db.execute("UPDATE schedule SET attempts=attempts+1 WHERE id=1")
                db.execute(
                    "UPDATE queue SET attempts=attempts+1, status='inflight', due=? WHERE task=?",
                    (now + delay, task),
                )
            with self._db() as db:
                deadline, stopped = db.execute(
                    "SELECT deadline, stopped FROM schedule WHERE id=1"
                ).fetchone()
            if self._stop.is_set() or stopped or self._now() >= deadline:
                return self.status()
            status, reason = "blocked", "ADAPTER"
            try:
                result = self.supervisor.step(task)
                if result in TERMINAL:
                    status, reason = result, ""
            except Failure as exc:
                reason = exc.code if exc.code in RETRYABLE else "REJECTED"
                status = "pending" if exc.code in RETRYABLE else "blocked"
            except OSError:
                status, reason = "pending", "IO"
            except Exception:
                # Do not persist arbitrary exception text, tokens or candidate-controlled labels.
                status, reason = "blocked", "ADAPTER"
            with self._db() as db, db:
                due = self._now() + delay
                if status == "pending" and attempts + 1 >= self.policy.attempts_per_task:
                    status, reason = "blocked", "LIMIT"
                db.execute(
                    "UPDATE queue SET status=?, reason=?, due=? WHERE task=?",
                    (status, reason, due, task),
                )
            return self.status()

    def run(self, *, max_polls: int = 4096) -> Status:
        """Run a finite polling session; retries and deadline also persist across sessions."""
        integer(max_polls, low=1, high=4096)
        for _ in range(max_polls):
            current = self.tick()
            if current.mode != "waiting":
                return current
            self._stop.wait(min(current.wait_ns / 1000000000, 0.25))
        return self.status()
