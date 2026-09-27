# Finite persistent worker scheduling

`worker_schedule.Schedule` services an explicit list of already admitted tasks. It adds local
waiting and retry limits to the [durable worker supervisor](worker-supervision.md). It cannot admit
tasks, reserve funds, approve artifacts or grant execution authority. The supervisor still checks
committed ownership and its durable execution record. Scheduling again cannot authorize uncertain
code to execute again.

Startup requires the original schedule identity and queue tables. A missing table or identity is
rejected rather than resetting deadlines, limits or pending work. Initial table and identity creation
are one transaction, so an interrupted first initialization leaves no half-created plan. Retain
damaged journals for recovery; these checks do not detect replacement by a valid older database.

## Fixed limits and identity

`Policy` fixes at most 128 unique task IDs, 32 calls per task, 4,096 calls in total and a duration
of one second to one hour. Defaults are eight calls per task, 128 total and five minutes. Each
call, including unsuccessful reads and reconciliation, consumes this local allowance before work
starts. These counts are separate from the consensus budget, which the scheduler cannot enlarge.

The private SQLite journal binds the policy, application origin, worker identity, worker journal
directory and monotonic clock epoch. Reopening preserves the deadline, counts, due times and stop
latch. A changed binding is rejected. Do not delete journals, invent a clock epoch or create a fresh
plan as a way to recover uncertain work. `clock_epoch` must identify the actual host boot associated
with the monotonic clock; Linux provides `/proc/sys/kernel/random/boot_id`. Tests inject a controlled
clock. Reboot changes the epoch and requires an operator recovery decision; translating deadlines
across boots and coordinated multi-host recovery are not implemented here.

## Waiting and outcomes

The exponential delay ceiling starts at `base_delay_ms` and doubles up to `max_delay_ms`. Each delay
is uniformly sampled between half that ceiling (at least one millisecond) and the ceiling, inclusive.
Due times persist before invocation and update after a returned failure. Time and randomness remain
outside consensus and signed commands.

| Observation | Action |
|---|---|
| `BUSY`, `NOT_READY`, `STALE`, `OUTCOME_UNKNOWN`, `PAUSED`, `CAPACITY` | Wait and service the same task within the fixed limits |
| Adapter `OSError` | Bounded wait; persisted evidence can be republished without code execution |
| Other protocol failure or exception | Block the task; retain only a fixed reason, never exception text |
| Finished or cancelled task | Stop scheduling it |
| Unknown execution result | Preserve uncertainty, stop scheduling it and report the plan as blocked |
| Process exit during invocation | Keep consumed allowance and in-flight record; the supervisor reconciles conservatively on a later eligible call |
| Limit or deadline exhausted | Stop admission and preserve the execution/command journals |

`tick()` makes at most one supervisor call. Its `Status(mode, attempts, remaining, wait_ns)` modes
are `waiting`, `complete`, `blocked`, `exhausted` and `stopped`. Complete describes the scheduling
plan, not artifact acceptance or mission success. Tasks run in due-time order, then original input
order. A delayed task does not starve another due task. A process lock enforces concurrency one.

`run(max_polls=4096)` runs a finite polling session, with interruptible waits of at most 250 ms.
It can return `waiting` when polling allowance ends; calling it again never resets persistent call
limits or the deadline. All I/O callbacks and the sandbox need their own finite limits. The local
deadline inhibits new calls; it cannot forcibly cancel an in-flight operation.

## Assembly and stop

```python
from pathlib import Path
from checkedflow.worker_schedule import Policy, Schedule

# Use a supervisor assembled with the operator's node, signer and gVisor-only executor.
schedule = Schedule(
    private_directory / "schedule",
    supervisor,
    Policy(tuple(approved_task_ids), total_attempts=32, duration_seconds=120),
    clock_epoch=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
)
result = schedule.run(max_polls=512)
```

No heartbeat thread starts automatically. An operator-owned bounded heartbeat caller uses the same
nonce coordinator as completion. Independent workers need different signing identities. `stop()`
immediately inhibits the local event and watchdog, then persists the stop latch. It needs no quorum,
but cannot revoke committed permissions or cancel dispatched requests. A failed persistence write
is reported while the process remains inhibited. There is no automatic local resume. Mission
pause/drain/resume remain signed commands; mission resume cannot clear the local stop latch.

## Evidence and remaining scope

Source tests use the real supervisor/command journals and benign trusted observers. They cover
restart limits, actual process exit, publication failures, deadline/stop checks, clock rollback,
identity/policy mismatch, fairness, unknowns and finite polling. The installed-wheel v2 CometBFT/gVisor
case now uses this scheduler for its admitted patch task; only a passing exact-artifact run qualifies
that integration.

This implements finite execution scheduling. Complete generation/verification/reuse orchestration,
cross-host identity ownership, boot recovery, verification admission policy, deployment and coordinated
restore remain separate requirements. These tests do not qualify all G1-G7 gates. Transport exceptions
outside the documented retry classes fail closed.
