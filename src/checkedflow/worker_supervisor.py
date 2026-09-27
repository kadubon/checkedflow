"""One bounded invocation per committed task attempt, with conservative crash recovery."""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from checkedflow.core.operational import State
from checkedflow.core.values import Failure, Object, names, require
from checkedflow.core.work_tasks import ROLE, Task, funding
from checkedflow.dispatch_watchdog import Watchdog
from checkedflow.wire import digest, document, dumps
from checkedflow.worker_submission import Coordinator

MAX_EVIDENCE_BYTES = 1048576
MAX_LOCAL_ATTEMPTS = 128


@dataclass(frozen=True)
class Outcome:
    """Evaluator-owned evidence; reporting does not imply verification or acceptance."""

    outcome: str
    evidence: bytes


class Supervisor:
    """Synchronous, concurrency-one execution with durable pre-invocation and result records.

    Supply a trusted bounded executor (for repository work, RepositoryExecutor) and a verified
    evidence publisher. Never pass a candidate-defined callback. Distinct workers need distinct
    signing identities. The local directory and all callbacks are operator-controlled.
    """

    def __init__(
        self,
        directory: Path,
        coordinator: Coordinator,
        watchdog: Watchdog,
        execute: Callable[[State, Task], Outcome],
        publish: Callable[[State, Task, bytes], str],
    ) -> None:
        require(
            watchdog.chain == coordinator.chain and watchdog.mission == coordinator.mission,
            "SCOPE",
            "supervisor watchdog differs from worker scope",
        )
        self.directory, self.coordinator, self.watchdog = directory, coordinator, watchdog
        self._execute, self._publish = execute, publish
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(directory / "worker.sqlite")) as db, db:
            db.execute("BEGIN IMMEDIATE")
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            require(
                not tables or tables == {"identity", "attempts"},
                "VERSION",
                "worker journal profile",
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1), "
                "binding BLOB NOT NULL)"
            )
            binding = dumps(
                {
                    "chain": coordinator.chain,
                    "mission": coordinator.mission,
                    "actor": coordinator.actor,
                    "version": "checkedflow/worker/v1",
                    "origin": coordinator.origin(),
                }
            )
            row = db.execute("SELECT binding FROM identity WHERE id=1").fetchone()
            if row is None:
                require(not tables, "STORAGE", "missing worker identity; recovery required")
                db.execute("INSERT INTO identity VALUES (1, ?)", (binding,))
            else:
                require(row[0] == binding, "SCOPE", "worker journal belongs to another identity")
            db.execute(
                "CREATE TABLE IF NOT EXISTS attempts (task TEXT PRIMARY KEY, fence INTEGER "
                "NOT NULL, revision INTEGER NOT NULL, outcome TEXT, evidence BLOB)"
            )

    @staticmethod
    def _task(state: State, identity: str) -> Task:
        task = next((task for task in state.tasks if task.identity == identity), None)
        require(task is not None, "NOT_FOUND", "task unavailable in active state")
        return cast(Task, task)

    def _command(self, state: State, kind: str, payload: Object) -> State:
        scope: Object = {"mission": state.mission}
        payload = scope | payload
        request = (
            f"{state.journal.epoch}:worker-"
            + digest(
                {
                    "actor": self.coordinator.actor,
                    "nonce": dict(state.journal.actors)[self.coordinator.actor] + 1,
                    "kind": kind,
                    "payload": payload,
                }
            )[:48]
        )
        return self.coordinator.send(request, kind, payload)

    def _ready(self, identity: str, fence: int | None = None) -> tuple[State, Task]:
        self.watchdog.poll()
        state = self.watchdog.current()
        task = self._task(state, identity)
        require(state.mode == "running", "PAUSED", "worker dispatch paused")
        if fence is not None:
            require(
                task.status == "running"
                and task.owner == self.coordinator.actor
                and task.revision == self.coordinator.revision
                and task.fence == fence
                and state.height < task.until,
                "FENCE",
                "execution ownership changed",
            )
            credential = next(
                (
                    item
                    for item in state.credentials
                    if item.identity == task.owner and item.revision == task.revision
                ),
                None,
            )
            require(
                credential is not None
                and credential.usable_at(state.height)
                and credential.role == ROLE[funding(state.budget, task).phase],
                "AUTHORITY",
                "execution credential unavailable",
            )
        return state, task

    def heartbeat(self, identity: str) -> State:
        """An optional caller may renew through the same serialized nonce coordinator."""
        require(self.coordinator.reconcile() != "pending", "OUTCOME_UNKNOWN", "pending command")
        state = self.coordinator.observe()
        task = self._task(state, identity)
        return self._command(state, "task.heartbeat", {"task": identity, "fence": task.fence})

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        lock = sqlite3.connect(self.directory / "worker-lock.sqlite", timeout=0)
        try:
            try:
                lock.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise Failure("BUSY", "worker already executing or reconciling") from exc
            yield
        finally:
            lock.close()

    def step(self, identity: str) -> str:
        """One bounded attempt or reconciliation. Caller controls finite scheduling/backoff."""
        with self._exclusive():
            return self._step(identity)

    def _room(self, identity: str) -> None:
        with closing(sqlite3.connect(self.directory / "worker.sqlite", timeout=0)) as db:
            exists = db.execute("SELECT task FROM attempts WHERE task=?", (identity,)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            require(
                exists is not None or count < MAX_LOCAL_ATTEMPTS,
                "CAPACITY",
                "local execution recovery capacity exhausted",
            )

    def retire(self, identities: tuple[str, ...]) -> int:
        """During a committed pause, retain exact finished evidence before retiring local buffers.

        Run before consensus history archival. Unknowns, running work and missing active-state
        proofs stay pinned. This removes recovery buffers, not signed history or artifact roots.
        """
        identities = names(list(identities), limit=MAX_LOCAL_ATTEMPTS)
        require(bool(identities), "SHAPE", "nonempty retirement selection required")
        with self._exclusive():
            require(self.coordinator.reconcile() != "pending", "OUTCOME_UNKNOWN", "pending command")
            state = self.coordinator.observe()
            require(state.mode == "paused", "PAUSED", "pause before local retirement")
            with closing(sqlite3.connect(self.directory / "worker.sqlite", timeout=0)) as db, db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                verified: list[Task] = []
                for identity in identities:
                    task = self._task(state, identity)
                    require(task.status == "finished", "PENDING", "known finished task required")
                    row = db.execute(
                        "SELECT fence, revision, outcome, evidence FROM attempts WHERE task=?",
                        (identity,),
                    ).fetchone()
                    if row is None:
                        continue
                    require(
                        row[:2] == (task.fence, task.revision)
                        and row[2] == "reported"
                        and isinstance(row[3], bytes)
                        and task.owner == self.coordinator.actor,
                        "BINDING",
                        "local result differs from committed completion",
                    )
                    evidence = cast(bytes, row[3])
                    require(
                        0 < len(evidence) <= MAX_EVIDENCE_BYTES
                        and sha256(evidence).hexdigest() == task.evidence,
                        "BINDING",
                        "local evidence differs from committed completion",
                    )
                    require(
                        self._publish(state, task, evidence) == task.evidence,
                        "BINDING",
                        "retained evidence differs",
                    )
                    verified.append(task)
                current = self.coordinator.observe()
                require(current.mode == "paused", "PAUSED", "mission resumed during retirement")
                for task in verified:
                    require(
                        self._task(current, task.identity) == task,
                        "CONFLICT",
                        "completion changed during retirement",
                    )
                    db.execute("DELETE FROM attempts WHERE task=?", (task.identity,))
                return len(verified)

    def _step(self, identity: str) -> str:
        require(self.coordinator.reconcile() != "pending", "OUTCOME_UNKNOWN", "pending command")
        state = self.coordinator.observe()
        task = self._task(state, identity)
        if task.status in {"finished", "unknown", "cancelled"}:
            return task.status
        self._room(identity)
        started_here = False
        if task.status in {"ready", "leased"}:
            state, task = self._ready(identity)
            if task.status == "ready":
                state = self._command(state, "task.lease", {"task": identity})
                task = self._task(state, identity)
            state = self._command(state, "task.start", {"task": identity, "fence": task.fence})
            task = self._task(state, identity)
            started_here = True
        require(
            task.owner == self.coordinator.actor and task.revision == self.coordinator.revision,
            "AUTHORITY",
            "task belongs to another worker or key revision",
        )
        with closing(sqlite3.connect(self.directory / "worker.sqlite", timeout=0)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            row = db.execute(
                "SELECT fence, revision, outcome, evidence FROM attempts WHERE task=?", (identity,)
            ).fetchone()
            if row is not None:
                require(row[:2] == (task.fence, task.revision), "FENCE", "local attempt differs")
            else:
                db.execute(
                    "INSERT INTO attempts VALUES (?, ?, ?, NULL, NULL)",
                    (identity, task.fence, task.revision),
                )
                db.commit()
            if row is None or row[2] is None:
                result = Outcome(
                    "unknown",
                    dumps({"reason": "worker_interrupted", "task": identity, "fence": task.fence}),
                )
                if row is None and started_here:
                    state, task = self._ready(identity, task.fence)
                    try:
                        result = self._execute(state, task)
                        require(
                            result.outcome in {"reported", "unknown"}, "SHAPE", "worker outcome"
                        )
                        require(
                            isinstance(result.evidence, bytes)
                            and 0 < len(result.evidence) <= MAX_EVIDENCE_BYTES,
                            "LIMIT",
                            "worker evidence ceiling",
                        )
                        document(result.evidence)
                    except Exception:
                        # Failed observation does not prove absence of execution.
                        result = Outcome(
                            "unknown",
                            dumps(
                                {
                                    "reason": "worker_observation_failed",
                                    "task": identity,
                                    "fence": task.fence,
                                }
                            ),
                        )
                db.execute(
                    "UPDATE attempts SET outcome=?, evidence=? WHERE task=?",
                    (result.outcome, result.evidence, identity),
                )
                db.commit()
            else:
                result = Outcome(row[2], bytes(row[3]))
        expected_fence = task.fence
        fingerprint = self._publish(state, task, result.evidence)
        require(
            fingerprint == sha256(result.evidence).hexdigest(),
            "BINDING",
            "published evidence differs",
        )
        current = self.coordinator.observe()
        task = self._task(current, identity)
        if task.status in {"finished", "unknown", "cancelled"}:
            return task.status
        require(
            task.status == "running"
            and task.owner == self.coordinator.actor
            and task.revision == self.coordinator.revision
            and task.fence == expected_fence,
            "FENCE",
            "completion ownership changed",
        )
        state = self._command(
            current,
            "task.finish",
            {
                "task": identity,
                "fence": task.fence,
                "outcome": result.outcome,
                "evidence": fingerprint,
            },
        )
        return self._task(state, identity).status
