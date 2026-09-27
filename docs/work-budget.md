# Governed work-budget ledger

Status: an unreleased v2 accounting component. It does not grant execution authority or
establish a task lifecycle. The frozen v1 runtime keeps its original accounting semantics.

The ledger answers three questions: how much funding was approved, how much remains reserved,
and how much has been charged. Generate, execute, verify and repair reservations use the same
finite budget. Each reservation binds a SHA-256 target identity and receives the signed command's
epoch-qualified request identity as its ticket. A ticket cannot be substituted for another target.

All three commands require the current signatures of at least three administrative organizations,
an administrative actor and the exact mission. A worker signature or agent transport token cannot
configure, reserve, refund or settle funding. These are accounting statements authorized by the
administrators; they do not measure physical compute use or independently prove what happened.

## Commands and balances

The [command schema](../src/checkedflow/data/budget-command.schema.json) describes `kind` and
`payload` inside the existing signed v2 envelope. Amounts are nonnegative interoperable integers;
the protocol does not prescribe a currency or convert these units into elapsed CPU time.

| Command | Payload fields in addition to `mission` | Effect |
|---|---|---|
| `budget.configure` | `budget`, `verification_reserve` | Set the immutable positive budget and a positive checking allocation no larger than that budget. Only an unconfigured empty ledger accepts this command. |
| `budget.reserve` | `phase`, `ceiling`, `target` | While the mission is running, reserve a positive ceiling for the declared phase and exact target digest. |
| `budget.settle` | `ticket`, `outcome`, `charged` | Settle one existing reservation, including while paused or draining. Terminal tickets cannot be rewritten. |

For example, configure 100 units with 30 protected for verification. Generation, execution and
repair together can initially reserve at most 70. Verification may use the protected allocation.
Releasing a verification reservation restores its protection. Completed or unknown verification
charges count toward the allocation already used; the same units are never available twice.

`spent` is inherited spending plus archived spending and active ticket charges. `reserved` is
inherited held reservations plus the ceilings of active open reservations.
`available = budget - spent - reserved`. Every transition and decoded state checks the overall
budget and the unused protected verification allocation. Costs cannot be negative or exceed a
ticket's ceiling. A single immutable budget prevents silently increasing the experiment's initial
resource envelope after observing its results.

## Uncertainty and settlement

Settlement has three explicit outcomes:

- `settled`: charge the authorized amount, at most the ceiling, and return the unused allocation.
- `unknown`: charge the full ceiling and retain an immutable unknown ticket. Unknown work is
  never treated as free merely because its actual execution cost could not be measured.
- `released`: charge zero and return the reservation. Administrators must establish that release
  is appropriate; this accounting component does not determine whether an external effect ran.

An unknown ticket cannot subsequently be changed to released or erased by these commands.
It remains visible after request-journal rollover. The [task ownership layer](work-tasks.md) now
attaches reserved tickets and prevents independent settlement of attached funding. This is not
yet the full task residual with
dependencies and recheck triggers; that integration is still required. A settlement likewise does
not establish checker acceptance, artifact adoption, compensation or permission to retry.

## Capacity, replay and compatibility

The current component retains at most 128 tickets, including terminal and unknown tickets.
When full, new reservations fail. Existing tickets can still settle; pause, drain, key revocation
and request-journal rollover remain separate control operations. Reservations consume ordinary
journal capacity. Configuration and settlement use the administrative reserve. A saturated journal
can require rollover before more controls, but the permanent rollover slot remains available.

[Settled-work retirement](work-archive.md) frees eligible terminal records only after epoch
retirement, preserving authenticated history and cumulative charges. Uncertain and reserved
funding cannot retire. This is not a budget refill or permission to discard unresolved obligations.

[Prepared legacy successors](legacy-migration.md) carry original charges and held reservations
in a separate immutable `inheritance` record. Current budget settlement and retirement commands
cannot reduce either inherited amount. The complete referenced old state remains necessary for
reconciliation; a zero reservation does not erase an already charged unknown outcome.

Repeated delivery of the exact signed request is acknowledged without another reservation or
charge. A conflicting request or reused retired epoch identity is rejected by the journal.
The v2 SQLite store commits ledger changes, signed commands and rollover archives atomically;
full history replay recomputes the same balances and state hash. Snapshot structure alone is not
an authenticated checkpoint.

The v2 state schema now includes a required `budget` member. This changes an unpublished
development format: earlier development control snapshots are not silently reinterpreted.
Published v1 histories and package behavior retain their original decoding and replay paths.

## Remaining task integration

A reservation is not a lease. The [task ownership layer](work-tasks.md) now binds a ticket to
approved workers, a purpose, attempt fence, deadline and result evidence identity. Dispatch must
still authenticate and resolve the immutable target contract before executing.
It must reserve adequate independent verification capacity for each task, not merely rely on the
mission-wide allocation. Automatic settlement, restart reconciliation, task residuals, artifact
acceptance, administrative emergency actions and history retention remain required release work.

[Inherited obligation reconciliation](legacy-reconciliation.md) now provides separate governed
adjustment of prepared legacy records. It does not create new executable tasks or release
funding through ordinary ticket settlement.
