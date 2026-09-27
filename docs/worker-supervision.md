# Durable worker submission and bounded execution

The draft v2 worker SDK connects a committed task to one bounded execution, evidence publication
and signed completion. It separates two operations that must not be confused: resending identical
signed command bytes can be idempotent; executing a program a second time can repeat its effects.

This component is synchronous, with one invocation at a time per worker. The
[finite persistent scheduler](worker-scheduling.md) adds bounded waiting and retries. Service deployment,
multi-host leader election, automatic generation loop and complete deployment lifecycle remain
separate requirements. Do not interpret this SDK as an unattended production service.

## Components and authority

| Component | Responsibility | Does not establish |
|---|---|---|
| `worker_submission.Coordinator` | One durable nonce stream for a worker's commands | Execution authority from an HTTP acknowledgment |
| `dispatch_watchdog.Watchdog` | Inhibit dispatch on stale, stopped or non-running observations | Consensus finality or an extended lease |
| `worker_supervisor.Supervisor` | Persist an attempt before invocation and its result before publication | Correctness or artifact acceptance |
| `repository_worker.RepositoryExecutor` | Bind a funded target to the existing gVisor-only repository observer | General test-suite validity or permission to open a PR |
| `repository_worker.EvidencePublisher` | Write and read back exact evidence through scoped storage | Required replicas or long-term availability |

Only operator-owned validating full nodes are trusted. Connect `Client.live_state` and `Client.submit`
to the coordinator. Supply a role-specific signer, such as the existing Vault adapter, bound to the
committed key revision. No administrative signing key is needed or accepted for worker commands.
All callers for one identity, including heartbeats, must use the same coordinator directory. Do not
run another coordinator on a different host with the same key. This local exclusion is not a global
lease on a signing identity. Independent workers should have distinct identities.

## Nonce and response recovery

The coordinator serializes callers with a SQLite write lock in `submission-lock.sqlite`; a separate
`submission.sqlite` durably commits original signed bytes before the first network call. A process
exit releases the lock but leaves the intent. The journal binds chain, mission and actor, pins the immutable application origin reconstructed
from the configured validating node's initial identity roots, organizations and journal limits, and records
monotonic observed height/nonce floors plus the same-height state hash. It retains one outstanding
command and the most recently resolved command, rather than an unbounded in-memory request list.

`send(request, kind, payload)` accepts only worker/verifier commands. It authenticates and evaluates
the proposed bytes against the current own-node state before writing the intent. Successful node
submission alone is insufficient: a matching committed request receipt must be observed. Missing,
rejected, malformed or lost replies leave uncertainty and block a different logical request.
Repeating the most recently confirmed request revalidates its original signed bytes against current
admission rules before returning a cached acknowledgment. A retired epoch or revoked signing key
cannot be bypassed by the local cache. Historical receipt reconciliation remains a separate operation.

`reconcile()` returns `idle`, `confirmed` or `pending`. It never treats absence in an active epoch as
proof that the command cannot still arrive. With `archive=...`, it accepts only the complete immediately
preceding request epoch anchored in the current own-node archive root. A matching member confirms
the request; verified nonmembership in that closed epoch returns `retired_absent`. That outcome frees
the nonce stream without asserting successful work. Reusing the retired request ID is rejected.
Older archives need a broader authenticated-chain recovery path, which this method does not provide.

`retransmit()` is explicit and limited to three total transport calls, counted durably before each
call. It sends the exact original bytes after checking current eligibility, or resolves an already
committed receipt without another send. It never signs a replacement or executes a program. Rotation,
expiry or a retired epoch can make retransmission invalid; keep the pending record for reconciliation.
Deleting the journal or using a fresh ID is not a recovery operation.

## Execution and failure windows

`Supervisor.step(task_id)` inspects current committed state. It leases an approved ready task if
needed, starts its owned lease, then records the attempt before invoking the trusted observer. It
rechecks own-node freshness, running mode, ownership, key revision, fence and lease height immediately
before invocation. The repository executor matches the funded contract digest and uses the existing
gVisor runner; there is no host fallback and candidate code receives no journal, signer or storage access.

| Failure window | Subsequent action |
|---|---|
| Before confirmed lease/start | Reconcile the original signed command; no execution without current authority |
| Start is committed, but no local execution record exists | Record unknown; do not assume execution never began |
| Attempt recorded, no result persisted | Record unknown interruption; never invoke that attempt again |
| Result persisted, evidence store unavailable | Retry publishing the same bytes; do not rerun the observer |
| Completion sent, reply lost | Reconcile the same request; committed terminal state prevents re-execution |
| Owner/fence changed or lease expired | Reject completion under stale authority; retain local evidence |

Observer exceptions, malformed evidence or evidence over 1 MiB produce bounded diagnostic evidence
and an unknown outcome without including exception text. Unknown work consumes the existing full
reserved ceiling. Reported work also consumes its reserved ceiling; it is not automatically accepted.
Independent verifiers still submit separate observations under the verification contract.

Pause and local watchdog stop inhibit new execution. A persisted result may still settle historical
work while the mission is paused, provided the core permits completion under the current fence.
`heartbeat(task_id)` uses the same nonce coordinator; a caller may schedule it during a bounded
invocation. No heartbeat thread or unbounded retry loop starts automatically. The core allows
heartbeats in running/draining modes; a stale or uncertain submission blocks other command sends.
Local stop cannot cancel already running code or manufacture a consensus revocation.

## SDK assembly

Install `checkedflow[distributed]` for the real CometBFT client. The following wiring assumes that
the operator already loaded the approved contract, exact input bytes, worker signer and scoped
artifact store. These values are not obtained from candidate output or remote discovery.

```python
from checkedflow.dispatch_watchdog import Watchdog
from checkedflow.repository_worker import EvidencePublisher, RepositoryExecutor
from checkedflow.worker_submission import Coordinator
from checkedflow.worker_supervisor import Supervisor


def assemble_worker(directory, client, signer, actor, revision, chain, mission,
                    base, patch, contract, cases, artifact_store, access):
    coordinator = Coordinator(
        directory / "commands", client.live_state, client.submit, signer,
        chain=chain, mission=mission, actor=actor, revision=revision,
    )
    watchdog = Watchdog(
        client.live_state, chain=chain, mission=mission,
        max_read_age_ns=5_000_000_000, max_stall_ns=5_000_000_000,
    )
    supervisor = Supervisor(
        directory / "execution", coordinator, watchdog,
        RepositoryExecutor(base, patch, contract, cases),
        EvidencePublisher(artifact_store, access),
    )
    return coordinator, watchdog, supervisor
```

`Coordinator.origin()` observes and pins the application origin; the execution journal includes
that same binding. Reusing a chain name with different initial ownership cannot import old execution
results. Routine application-key rotation preserves the initial roots. This continuity check does
not replace independent validator/bootstrap trust. Older local journal layouts are rejected and
need an explicit recovery/migration procedure; never delete pending intent to upgrade.

The caller must warm the watchdog with two fresh observations showing committed-height progress
before calling `step`. Choose read timeouts shorter than the freshness window; each callback must
be bounded. Use the finite scheduler or an equivalently bounded caller outside consensus. On `OUTCOME_UNKNOWN`, inspect
`pending()` and reconcile; do not blindly loop `step`, delete state or restart under another identity.
The [machine-readable SDK catalogue](../src/checkedflow/data/worker-operations.json) distinguishes
filesystem writes, network effects and retry behavior. There is no new worker CLI command in this increment.

## Persistence and qualification limits

The execution journal permits at most 128 local attempt records. Capacity is checked before a new
lease/start, so a full journal does not authorize execution or consume another task merely to make
room. Existing attempts can still be reconciled. The limit bounds logical recovery records and their
1 MiB evidence ceiling, not total artifact storage, SQLite overhead or a host disk quota.

During a committed mission pause, `Supervisor.retire((task_id, ...))` can retire local buffers for
explicitly selected tasks still present as `finished` in current own-node state. It matches owner,
revision, fence and evidence digest, republishes the exact bytes through the configured verified
publisher, and rechecks the paused state and unchanged completion before an atomic local deletion.
A publication failure leaves every selected buffer intact. The return value is the number removed;
repeating the operation for an active finished task already retired returns zero.

Perform this maintenance **before** the administrators archive those tasks with `history.archive`.
Missing active-state proof, an uncertain command, a running/unknown task, mismatched bytes, or a
mission resumed during publication prevents retirement. There is no absence-based deletion or
automatic reset of the journal. Records whose tasks were already archived need a future authenticated
archive recovery path; they cannot be cleared by this method. Unknown obligations remain pinned.

A `finished` work receipt means a reported execution completed, not that its candidate was accepted.
The operation can retain evidence for a quarantined candidate without making it reusable. It does
not remove signed history or artifact-retention roots, reset costs, establish replicas, or change
the rule against repeating started work. Routine key rotation does not erase historical records.

Directories must be private, operator-controlled and protected by appropriate Windows ACLs or POSIX
permissions. They are local recovery state, not portable untrusted import formats. Private signing
keys are not stored, but signed commands and evidence can contain confidential mission information.
Do not expose them as public artifacts. Filesystem loss, copying a journal to two active workers,
rollback of local recovery state and coordinated worker-journal backup remain deployment concerns.

Source tests include actual child-process exits before/after command commitment and during execution
or before evidence publication, signed nonce sequencing, contention, rejected scope, restart and
unknown accounting. Benign observer fixtures do not execute candidate code. The installed-wheel
CometBFT/gVisor qualification case connects these components to actual isolated repository work,
one-node interruption and a restart that must not repeat execution. Only an executed exact-artifact
run qualifies that case. This does not complete generation/reuse loops, external-effect authority,
full G6, rolling upgrades or the remaining release requirements.


### Independent container recovery

The runner now requires the separately running [sandbox recovery service](sandbox-recovery.md).
It records creation intent and immutable container ownership before execution. The service can
remove an owned expired container after abrupt worker death. The worker command/execution journals
still serve a different purpose: they prevent repeat execution and retain uncertain outcomes.
An unknown result is not proof of process termination. Read the recovery service's daemon failure,
service availability and remaining deployment qualification limits before enabling unattended work.

The coordinator also accepts the purpose-scoped `effect.reserve` and `effect.report` commands.
They retain the same nonce, original-byte and lost-reply rules. The task Supervisor does not thereby
become an external-effect executor: [effect orchestration](work-effects.md) must separately enforce
policy, current reservation, provider-journal ownership and send-time freshness.
The separate [effect supervisor](effect-supervision.md) now performs ordinary effect reservation,
one supervised invocation, verified observation publication and signed reporting. Its local claim
and provider journal are independent of the task execution journal; never substitute one for another.
