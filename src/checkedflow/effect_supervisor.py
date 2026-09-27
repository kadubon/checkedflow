"""Durable reserve, supervised dispatch, evidence publication and signed reporting.

One protected local journal and one nonce coordinator belong to an exclusive executor.
Interrupted sends become unknown; recovery never invokes the provider again. This is not
cross-host journal failover, governed remote reconciliation or compensation.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager, nullcontext
from io import BytesIO
from pathlib import Path
from typing import cast

from checkedflow.artifact_io import verify
from checkedflow.core.artifact import Reference
from checkedflow.core.operational import State
from checkedflow.core.values import Failure, Object, fields, integer, require, text
from checkedflow.core.work_effects import Effect
from checkedflow.domains.repository_patch import Contract, digest_bytes
from checkedflow.effect_dispatch import Dispatcher
from checkedflow.github_drafts import Outcome
from checkedflow.github_effects import Intent
from checkedflow.repository_reuse import Inputs, contract_digest, prepare
from checkedflow.telemetry import Recorder
from checkedflow.wire import document, dumps
from checkedflow.worker_submission import Coordinator

MAX_OPERATIONS = 64
MAX_OBSERVATION = 8192
PROFILE = "checkedflow/effect-observation/v1"
UNKNOWN_REASONS = frozenset(
    {"provider_unknown", "dispatch_failed", "executor_interrupted", "unowned_reservation"}
)


class Supervisor:
    """Concurrency-one synchronous execution; callers schedule bounded steps explicitly.

    Begin with an authorized effect. An existing reservation without this journal cannot
    be adopted for sending. Keep all three journals (supervisor, coordinator, provider)
    together under exclusive ownership; never recreate them to retry an uncertain effect.
    """

    def __init__(
        self,
        directory: Path,
        coordinator: Coordinator,
        dispatcher: Dispatcher,
        *,
        recorder: Recorder | None = None,
    ) -> None:
        require(
            coordinator.chain == dispatcher.watchdog.chain
            and coordinator.mission == dispatcher.watchdog.mission
            and coordinator.actor == dispatcher.executor
            and coordinator.revision == dispatcher.revision,
            "SCOPE",
            "effect supervisor identities differ",
        )
        self.recorder = recorder
        self.directory, self.coordinator, self.dispatcher = directory, coordinator, dispatcher
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        provider = dispatcher.provider
        binding = dumps(
            {
                "profile": "checkedflow/effect-executor/v1",
                "origin": coordinator.origin(),
                "chain": coordinator.chain,
                "mission": coordinator.mission,
                "actor": coordinator.actor,
                "repository": provider.repository,
                "repository_id": provider.repository_id,
                "provider_actor": provider.actor,
            }
        )
        with self._exclusive(), self._db() as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            require(
                not tables or tables == {"identity", "operations"}, "VERSION", "executor journal"
            )
            db.execute("CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY, binding BLOB)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS operations (effect TEXT PRIMARY KEY, binding BLOB "
                "NOT NULL, phase TEXT NOT NULL, reserve_request TEXT NOT NULL, "
                "report_request TEXT NOT NULL, started INTEGER NOT NULL, evidence BLOB)"
            )
            row = db.execute("SELECT binding FROM identity WHERE id=1").fetchone()
            if row is None:
                require(not tables, "STORAGE", "executor journal identity missing")
                db.execute("INSERT INTO identity VALUES (1, ?)", (binding,))
            else:
                require(row[0] == binding, "SCOPE", "executor journal belongs to another origin")

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with closing(sqlite3.connect(self.directory / "effect-lock.sqlite", timeout=0)) as lock:
            try:
                lock.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                raise Failure("BUSY", "effect executor already active") from None
            yield

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.directory / "effects.sqlite", timeout=0)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db

    @staticmethod
    def _effect(state: State, identity: str) -> Effect:
        effect = next((item for item in state.effects if item.identity == identity), None)
        require(effect is not None, "NOT_FOUND", "effect not retained in active state")
        return cast(Effect, effect)

    def _binding(self, effect: Effect, intent: Intent, contract: Contract, inputs: Inputs) -> bytes:
        require(
            effect.intent == intent.digest and intent.target == contract_digest(contract),
            "BINDING",
            "executor arguments differ from approved intent",
        )
        require(
            effect.executor == self.coordinator.actor
            and effect.revision == self.coordinator.revision,
            "AUTHORITY",
            "effect belongs to another executor revision",
        )
        require(len(inputs.evidence) <= 12, "LIMIT", "effect evidence inventory bound")
        binding: Object = {
            "operation": effect.operation,
            "authorization": effect.authorization,
            "executor": effect.executor,
            "revision": effect.revision,
            "intent": intent.record(),
            "base": inputs.base.record(),
            "patch": inputs.patch.record(),
            "inventory": inputs.inventory.record(),
            "evidence": [ref.record() for ref in inputs.evidence],
        }
        if self.dispatcher.staging:
            binding["staging"] = True
        return dumps(binding)

    @staticmethod
    def _request(state: State, effect: Effect, kind: str) -> str:
        return f"{state.journal.epoch}:effect-{kind}-" + effect.operation[:40]

    def _observation(
        self, effect: Effect, intent: Intent, height: int, outcome: Outcome, reason: str
    ) -> bytes:
        require(outcome.status in {"confirmed", "unknown"}, "SHAPE", "provider outcome")
        number = integer(outcome.number, low=1 if outcome.status == "confirmed" else 0)
        require(outcome.status == "confirmed" or number == 0, "SHAPE", "unknown object number")
        return dumps(
            {
                "profile": PROFILE,
                "chain": self.coordinator.chain,
                "mission": self.coordinator.mission,
                "effect": effect.identity,
                "executor": effect.executor,
                "revision": effect.revision,
                "fence": effect.fence,
                "reserved": effect.reserved,
                "not_before_height": height,
                "plan": intent._plan(effect.operation, effect.authorization).record(),
                "outcome": "observed" if outcome.status == "confirmed" else "unknown",
                "number": number,
                "reason": reason,
            }
        )

    def _validate(self, raw: bytes, effect: Effect, intent: Intent, state: State) -> Object:
        require(0 < len(raw) <= MAX_OBSERVATION, "LIMIT", "effect observation ceiling")
        row = document(raw)
        fields(
            row,
            "profile chain mission effect executor revision fence reserved not_before_height "
            "plan outcome number reason",
        )
        outcome = text(row["outcome"])
        require(outcome in {"observed", "unknown"}, "SHAPE", "effect observation outcome")
        status = "confirmed" if outcome == "observed" else "unknown"
        reason = text(row["reason"])
        require(
            reason == "provider_confirmed" if status == "confirmed" else reason in UNKNOWN_REASONS,
            "SHAPE",
            "effect observation reason",
        )
        expected = self._observation(
            effect,
            intent,
            integer(row["not_before_height"], low=effect.reserved, high=state.height),
            Outcome(status, integer(row["number"])),
            reason,
        )
        require(raw == expected, "BINDING", "stored observation differs from original effect")
        return row

    def observation(self, identity: str) -> bytes | None:
        """Read local evidence for governed recovery; this does not confirm a committed report."""
        with self._exclusive(), self._db() as db:
            row = db.execute(
                "SELECT evidence FROM operations WHERE effect=?", (identity,)
            ).fetchone()
            return None if row is None or row[0] is None else bytes(row[0])

    def step(self, identity: str, intent: Intent, contract: Contract, inputs: Inputs) -> str:
        """Reserve once, dispatch at most once, then retain evidence and report original bytes."""
        observed = self.recorder.measure("effect.step") if self.recorder else nullcontext()
        with observed, self._exclusive():
            return self._step(identity, intent, contract, inputs)

    def _step(self, identity: str, intent: Intent, contract: Contract, inputs: Inputs) -> str:
        require(self.coordinator.reconcile() != "pending", "OUTCOME_UNKNOWN", "pending command")
        state = self.coordinator.observe()
        effect = self._effect(state, identity)
        binding = self._binding(effect, intent, contract, inputs)
        with self._db() as db:
            row = db.execute(
                "SELECT binding, phase, reserve_request, report_request, started, evidence "
                "FROM operations WHERE effect=?",
                (identity,),
            ).fetchone()
            if row is None:
                if effect.status not in {"authorized", "dispatch_reserved"}:
                    return effect.status
                require(
                    db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] < MAX_OPERATIONS,
                    "CAPACITY",
                    "effect recovery journal capacity exhausted",
                )
                owned = effect.status == "authorized"
                phase = "prepared" if owned else "dispatching"
                request = self._request(state, effect, "reserve") if owned else ""
                db.execute(
                    "INSERT INTO operations VALUES (?,?,?,?,?,?,NULL)",
                    (identity, binding, phase, request, "", state.height),
                )
                row = (binding, phase, request, "", state.height, None)
            require(row[0] == binding, "BINDING", "retained effect arguments changed")
            phase, reserve_request, report_request, started, evidence = row[1:]
            require(
                phase in {"prepared", "dispatching", "observed", "reported"},
                "STORAGE",
                "invalid executor phase",
            )
        if phase == "reported":
            require(isinstance(evidence, bytes), "STORAGE", "reported observation missing")
            self._validate(evidence, effect, intent, state)
            require(
                effect.status not in {"authorized", "dispatch_reserved"},
                "CONFLICT",
                "committed report is no longer present",
            )
            return effect.status
        if phase == "prepared":
            if effect.status == "authorized":
                self.dispatcher.watchdog.poll()
                current = self.dispatcher.watchdog.current()
                require(self._effect(current, identity) == effect, "CONFLICT", "effect changed")
                policy = self.dispatcher.policy.authorize(
                    current,
                    intent,
                    self.dispatcher.provider,
                    self.coordinator.actor,
                    self.coordinator.revision,
                    staging=self.dispatcher.staging,
                )
                require(policy == effect.policy, "POLICY", "approved policy differs")
                require(self.dispatcher.provider.enabled, "DISABLED", "provider disabled")
                self.dispatcher.access.authorize(state.mission, "write")
                prepare(
                    current,
                    effect.candidate,
                    contract,
                    inputs,
                    self.dispatcher.store,
                    access=self.dispatcher.access,
                )
                self.dispatcher.watchdog.current()
                state = self.coordinator.send(
                    reserve_request,
                    "effect.reserve",
                    {"mission": state.mission, "effect": identity},
                )
                effect = self._effect(state, identity)
            elif effect.status == "dispatch_reserved":
                # A matching retained completed command is checked locally by Coordinator.
                # Without it, preflight rejects reserve on an already reserved effect, before
                # any transmission. Merely seeing somebody else's reservation is insufficient.
                state = self.coordinator.send(
                    reserve_request,
                    "effect.reserve",
                    {"mission": state.mission, "effect": identity},
                )
                effect = self._effect(state, identity)
            if effect.status != "dispatch_reserved":
                return "reconciliation_required"
            # Only the coordinator's retained original reservation can establish that this
            # journal owns a not-yet-invoked dispatch. Never adopt a different reservation.
            with self._db() as db:
                db.execute(
                    "UPDATE operations SET phase='dispatching', started=? WHERE effect=?",
                    (state.height, identity),
                )
            started = state.height
            try:
                outcome = self.dispatcher.dispatch(identity, intent, contract, inputs)
                reason = (
                    "provider_confirmed" if outcome.status == "confirmed" else "provider_unknown"
                )
                evidence = self._observation(effect, intent, started, outcome, reason)
            except Exception:
                evidence = self._observation(
                    effect, intent, started, Outcome("unknown"), "dispatch_failed"
                )
        elif phase == "dispatching":
            evidence = self._observation(
                effect,
                intent,
                started,
                Outcome("unknown"),
                "executor_interrupted" if reserve_request else "unowned_reservation",
            )
        require(isinstance(evidence, bytes), "STORAGE", "effect observation missing")
        with self._db() as db:
            db.execute(
                "UPDATE operations SET phase='observed', evidence=? WHERE effect=?",
                (evidence, identity),
            )
        state = self.coordinator.observe()
        effect = self._effect(state, identity)
        observation = self._validate(evidence, effect, intent, state)
        ref = Reference(
            "sha256",
            digest_bytes(evidence),
            len(evidence),
            "application/json",
            "evidence",
            state.mission,
            effect.intent,
        )
        self.dispatcher.store.put(ref, BytesIO(evidence), access=self.dispatcher.access)
        verify(ref, self.dispatcher.store.get(ref, access=self.dispatcher.access))
        state = self.coordinator.observe()
        effect = self._effect(state, identity)
        if effect.evidence == ref.digest:
            with self._db() as db:
                db.execute("UPDATE operations SET phase='reported' WHERE effect=?", (identity,))
            return effect.status
        if effect.status != "dispatch_reserved":
            return "reconciliation_required"
        if not report_request:
            report_request = self._request(state, effect, "report")
            with self._db() as db:
                db.execute(
                    "UPDATE operations SET report_request=? WHERE effect=?",
                    (report_request, identity),
                )
        state = self.coordinator.send(
            report_request,
            "effect.report",
            {
                "mission": state.mission,
                "effect": identity,
                "fence": effect.fence,
                "outcome": observation["outcome"],
                "number": observation["number"],
                "evidence": ref.digest,
            },
        )
        with self._db() as db:
            db.execute("UPDATE operations SET phase='reported' WHERE effect=?", (identity,))
        return self._effect(state, identity).status
