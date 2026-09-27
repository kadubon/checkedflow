# Durable effect supervision

Status: development SDK orchestration. Real provider credential custody,
cross-host recovery, compensation and complete G1–G7 qualification remain unfinished.

## Why a saved provider receipt is not enough

A process may stop after GitHub accepted a request but before its result was saved or reported.
Starting the same operation again would risk another external action. A storage failure can also
prevent publication of the observation even when the original request succeeded. These are
separate recovery problems, with separate records.

`checkedflow.effect_supervisor.Supervisor` connects an authorized effect to its
[supervised dispatcher](effect-dispatch.md), a scoped artifact store and the
[original-byte nonce coordinator](worker-supervision.md). One operator-owned directory and
exclusive executor identity serialize calls. It never runs generated code on the host.

## API and ownership

Construct `Supervisor(directory, coordinator, dispatcher)` inside the executor service. Chain,
mission, executor identity and signing revision must agree. The journal also pins the validating
node's application origin and exact provider repository/account. Candidate code and ordinary
agent clients must not control this directory, the policy, the provider journal or signing key.

Call `step(effect_id, intent, contract, inputs)` beginning with a quorum-authorized effect.
The supervisor retains immutable arguments, verifies current policy/evidence/freshness, then
uses `effect.reserve` through the coordinator. It records that invocation may begin **before**
calling the dispatcher. At most one provider invocation is allowed for that retained record.

The coordinator's retained original reservation must prove ownership when recovering a local
intent. A reservation created by another caller cannot fill a local intent merely because it
names the same effect. Resolve a prepared operation before using the same signing coordinator
for unrelated work; a replaced/retired original-command receipt can require governed recovery.
An existing reservation with no local record is preserved as `unknown`, never adopted for sending.

All three journals are required: supervisor, signed-command coordinator and provider. Losing,
copying or rolling one back is not a recovery technique. This module has no cross-host shared
lock or failover guarantee. Local history is bounded to 64 operations and does not evict unknowns.
Automatic local retirement is not implemented; reaching the bound denies new work.

## Ordering and crash behavior

| Local durable phase | What has been retained | Recovery action |
|---|---|---|
| `prepared` | Original arguments and reservation request ID | Confirm the original signed reservation; dispatch only if no invocation has begun and current checks pass |
| `dispatching` | Invocation may have begun | Store an interrupted/unknown observation; do not call the dispatcher again |
| `observed` | Canonical observation bytes | Publish/read back the same bytes, then submit the same report identity; do not call the provider again |
| `reported` | Report completion observed in own-node state | Return current consensus classification; retain local evidence |

These are local journal phases, not the consensus effect states. For example, locally stored
`observed` bytes can coexist with a consensus `dispatch_reserved` or expired `unknown` effect.
The full modeled cost remains charged once reservation is committed.

The supervisor reads back and verifies published evidence before signing `effect.report`.
A mere storage acknowledgment cannot authorize that report. If reporting loses its reply, the
coordinator retains original signed bytes. A later step first reconciles those bytes against
own-node receipts. Unconfirmed submission blocks later commands; only explicit bounded
`coordinator.retransmit()` sends those exact command bytes again. It never repeats the provider
operation. A signer outage before transmission retains the report ID and saved observation.

Pause or a disabled destination policy can prevent new dispatch without preventing historical
reporting under still-valid reporting authority. If the reservation window or credential is no
longer valid, retain the result and use governed reconciliation. The supervisor does not grant
itself administrative reconciliation permission.

## Evidence and status

`observation(effect_id)` returns retained local bytes or `None`; it does not establish that those
bytes were published or committed. Discover their closed contract with
`checkedflow schema effect-observation`. The record binds chain, mission, effect, executor/revision,
fence, reservation height and the complete original provider plan to an outcome, object number
and bounded reason. Its artifact reference uses kind `evidence`, mission scope and the approved
intent digest as manifest. No exception text, token, filesystem path or remote response body is
copied into this record.

`not_before_height` is the lower bound on the local invocation/observation, not a wall-clock
timestamp or proof of when GitHub created an object. A provider's confirmed receipt is a
historical observation. Consensus signatures attribute the assertion; they do not prove remote
truth or ongoing existence. Unknown observations contain number zero and a reason such as
`executor_interrupted`, `unowned_reservation`, `provider_unknown` or `dispatch_failed`.

The step returns the current effect classification when available. `reconciliation_required`
means a retained local intent/result cannot proceed through ordinary reporting. `OUTCOME_UNKNOWN`
as an exception means a signed command remains uncertain; inspect the coordinator before further
steps. Never assign a new operation ID, discard the evidence or refund possibly spent budget.

## Verification scope

Source tests exercise lost command replies, explicit original-byte retransmission, wrong ownership,
policy withdrawal, publication failure, signer outage, expired reporting, argument changes,
concurrent callers and journal capacity. Actual child processes exit after reservation, after
invocation becomes possible, after the fixture provider response, during evidence publication and
after report commit. Recovery checks that no second provider invocation occurs.

The added installed infrastructure case uses real four-node CometBFT, four independent gVisor
checks, SQLite evidence, current policy and a lost committed report reply. Its provider is a fixed
protocol fixture, not GitHub. The required infrastructure gate includes that named case; an older
26-case report cannot qualify it. Complete G3 also needs the authorized live-provider path and
remaining multi-host staging and compensation recovery qualification. Historical inspection and unsigned
[reconciliation proposals](effect-reconciliation.md) are a separate operator SDK path; they do not
reuse the supervisor's execution permission or automatically submit an administrative command.
