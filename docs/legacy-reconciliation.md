# Reconcile inherited obligations

Successor preparation retains a bounded inventory of old funded tasks and uncertain attempts
for the selected mission. Each record binds the original task identity and complete task hash
to the independently anchored legacy snapshot. At most 128 records are accepted; preparation
refuses a larger inventory instead of silently dropping obligations. These records never become
executable v2 tasks or authorize another external call.

`budget.reconcile_inherited` requires the current new-chain administrative quorum, mission,
command identity and nonce. Its payload is:

```json
{
  "mission": "new-mission",
  "checkpoint": "<original legacy state hash>",
  "task": "original-task-id",
  "outcome": "unknown",
  "evidence": "<retained reconciliation evidence hash>"
}
```

The old checkpoint must match the approved inherited root. The selected task must exist in the
prepared inventory. Evidence is a digest of the operator's retained observations; signatures
authorize the accounting decision but cannot prove an external event happened. Retain the
actual evidence and complete old history before signing. The command is available while paused
so operators do not need to enable dispatch to resolve accounting. Transport clients need the
`operate` grant, which does not replace three current administrative organizations' signatures.

| Observation | Previously held reservation | Already charged old unknown |
|---|---|---|
| `unknown` | Charge its full ceiling and end the reservation; keep the outcome unresolved | Do not charge again; keep unresolved |
| `executed` | Charge its full ceiling and end the reservation | Do not charge again |
| `not_executed` as the first observation | Release the reservation without a new charge | Do not refund previous charges |
| `not_executed` after `unknown` | Keep the conservative charge already made | Do not refund previous charges |

An unresolved record accepts one first observation. An `unknown` outcome can later become
`executed` or `not_executed`; a final outcome cannot be changed. Repeated identical signed
commands follow normal journal idempotency. A different command cannot settle the same final
record again. Original inherited budget/spent/reserved fields remain immutable historical
values; current totals add recorded charges and subtract reservations resolved by this command.
No additional allowance is created. Ordinary ticket settlement cannot change these records.

The command and every observation remain in signed history. Replaying SQLite history or restoring
a verified application backup reproduces accounting. A reconciled state cannot be reintroduced
as fresh store genesis. Earlier development preparation bytes without an obligation inventory
retain their held funding but cannot use this command; do not fabricate inventory after activation.
New preparation changes the approved successor hash, so review and sign the new exact plan.

This accounting mechanism does not automatically clear old residuals, certify withdrawn artifacts,
retry uncertain effects, establish retained-object availability, or transfer validator custody.
Those obligations remain in the complete old snapshot and require their respective governed
procedures. It is a migration component, not proof of completed physical cutover or G1-G7.
