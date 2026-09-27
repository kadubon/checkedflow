"""Bounded service observations; readiness never grants execution authority.

All exported state measurements are gauges of one current snapshot, not incremented event
counters. Repeated scraping or replay therefore cannot count a committed operation twice.
"""

from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass

from checkedflow.core.operational import State
from checkedflow.core.values import Object, require
from checkedflow.core.work_acceptance import MAX_CANDIDATES
from checkedflow.core.work_acceptance import status as candidate_status
from checkedflow.core.work_effects import MAX_EFFECTS, STATUSES, UNRESOLVED
from checkedflow.core.work_tasks import MAX_TASKS, funding
from checkedflow.dispatch_watchdog import Watchdog
from checkedflow.telemetry import Recorder

REQUIRED = {
    "gateway": frozenset({"configuration", "storage"}),
    "worker": frozenset({"configuration", "storage", "signer", "sandbox"}),
    "verifier": frozenset({"configuration", "storage", "signer", "sandbox"}),
    "effect_executor": frozenset({"configuration", "storage", "signer", "provider"}),
    "validator": frozenset({"configuration", "storage", "signer", "backup"}),
}
TASK_STATUSES = ("ready", "leased", "running", "finished", "unknown", "cancelled")


def gauges(state: State) -> dict[str, int]:
    """Only implemented, measurable v2 quantities; absent instrumentation is not zero."""
    require(state.profile == "checkedflow/control-state/v2", "VERSION", "v2 observations required")
    ordinary = [receipt for receipt in state.journal.receipts if not receipt.administrative]
    administrative = [receipt for receipt in state.journal.receipts if receipt.administrative]
    values = {
        "committed_height": state.height,
        "request_epoch": state.journal.epoch,
        "active_request_receipts": len(state.journal.receipts),
        "ordinary_request_slots_available": state.journal.limits.ordinary_count - len(ordinary),
        "administrative_request_slots_available": state.journal.limits.administrative_count
        - len(administrative),
        "ordinary_request_bytes_available": state.journal.limits.ordinary_bytes
        - sum(len(r.encoded()) for r in ordinary),
        "administrative_request_bytes_available": state.journal.limits.administrative_bytes
        - sum(len(r.encoded()) for r in administrative),
        "task_slots_available": MAX_TASKS - len(state.tasks),
        "candidate_slots_available": MAX_CANDIDATES - len(state.candidates),
        "effect_slots_available": MAX_EFFECTS - len(state.effects),
        "tasks_lease_expired": sum(task.reason == "lease_expired" for task in state.tasks),
        "budget_spent_units": state.budget.spent,
        "budget_reserved_units": state.budget.reserved,
        "budget_available_units": state.budget.available,
        "budget_protected_verification_units": state.budget.protected_verification,
        "verification_backlog": sum(
            task.status in {"ready", "leased", "running"}
            and funding(state.budget, task).phase == "verify"
            for task in state.tasks
        ),
        "accepted_candidates": sum(
            candidate_status(candidate, state.credentials, state.height) == "accepted"
            for candidate in state.candidates
        ),
        "unresolved_effects_oldest_blocks": max(
            (
                state.height - effect.reserved
                for effect in state.effects
                if effect.status in UNRESOLVED
            ),
            default=0,
        ),
    }
    for mode in ("running", "paused", "draining"):
        values["mission_" + mode] = int(state.mode == mode)
    for status in TASK_STATUSES:
        values["tasks_" + status] = sum(task.status == status for task in state.tasks)
    for status in sorted(STATUSES):
        values["effects_" + status] = sum(effect.status == status for effect in state.effects)
    return values


@dataclass(frozen=True)
class Observation:
    role: str
    readable: bool
    checks: tuple[tuple[str, bool], ...]
    values: tuple[tuple[str, int], ...]

    @property
    def ready(self) -> bool:
        return self.readable and all(passed for _, passed in self.checks)

    def record(self) -> Object:
        return {
            "profile": "checkedflow/service-observation/v1",
            "role": self.role,
            "live": True,
            "readable": self.readable,
            "ready": self.ready,
            "checks": {name: value for name, value in self.checks},
            "gauges": {name: value for name, value in self.values},
        }

    def prometheus(self) -> str:
        values = dict(self.values) | {
            "process_live": 1,
            "node_readable": int(self.readable),
            "protected_work_ready": int(self.ready),
        }
        values.update({"dependency_" + name + "_ready": int(ok) for name, ok in self.checks})
        lines: list[str] = []
        for key, value in sorted(values.items()):
            name = "checkedflow_" + key
            lines.extend((f"# TYPE {name} gauge", f"{name} {value}"))
        return "\n".join(lines) + "\n"


class Monitor:
    """Observe one configured service role with bounded, read-only dependency probes.

    Probe callables are trusted local adapters, never client claims. They must enforce I/O
    deadlines and return exactly True for success. They must neither sign nor execute work.
    This component creates no threads, listeners, exporters or external telemetry connections.
    """

    def __init__(
        self,
        watchdog: Watchdog,
        role: str,
        probes: Mapping[str, Callable[[], bool]],
        *,
        recorder: Recorder | None = None,
    ) -> None:
        require(role in REQUIRED, "SHAPE", "known operational service role required")
        require(set(probes) == REQUIRED[role], "CONFIGURATION", "complete role probes required")
        require(
            all(callable(probe) for probe in probes.values()), "CONFIGURATION", "probe callable"
        )
        self.recorder = recorder
        self.watchdog, self.role = watchdog, role
        self.probes = tuple(sorted(probes.items()))

    def observe(self) -> Observation:
        observed = self.recorder.measure("service.observe") if self.recorder else nullcontext()
        with observed:
            return self._observe()

    def _observe(self) -> Observation:
        try:
            state = self.watchdog.poll()
            values = tuple(sorted(gauges(state).items()))
            readable = True
        except Exception:
            # No stale previous snapshot or raw dependency error is exposed on failed reads.
            values, readable = (), False
        checks = []
        for name, probe in self.probes:
            try:
                passed = probe() is True
            except Exception:
                passed = False
            checks.append((name, passed))
        try:
            self.watchdog.current()
            fresh = True
        except Exception:
            fresh = False
        checks.append(("node_fresh", fresh))
        return Observation(self.role, readable, tuple(checks), values)
