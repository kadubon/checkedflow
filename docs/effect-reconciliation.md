# Observe an uncertain draft operation without sending it again

A lost reply does not establish whether GitHub created a draft PR. The operator SDK
`effect_reconciliation.Reconciler` inspects the **original** operation and publishes an observation
for administrative review. It sends only GitHub GET requests. It does not create a branch, open or
close a PR, sign a command, submit a transaction or change the provider's dispatch journal.

This is the read-and-review path for the [effect state machine](work-effects.md). Ordinary live
reporting remains the responsibility of the [effect supervisor](effect-supervision.md).

## Inputs and sequence

Supply the original executor's `Coordinator`, a `Drafts` adapter with its fixed repository ID and
actor, a scoped artifact store, an operator-owned `Access` grant and the exact approved `Policy`
snapshot. Preserve that snapshot separately from later changes to the live dispatch policy. Its
canonical digest must match the quorum-approved effect, including provider actor and destination.
Reading an old snapshot never authorizes dispatch under it. The coordinator's reader must
use the operator's validating full node. It pins application origin and observed height/nonce floors.
This method does not invoke its signer. GitHub reads require a separately protected credential with
the relevant repository read permissions; candidate programs and agent clients receive no credential.

```python
from checkedflow.effect_reconciliation import Reconciler
from checkedflow.wire import document

reader = Reconciler(coordinator, provider, store, access, approved_policy)
proposal = reader.collect(effect_id, intent, contract, inputs.base, inputs.patch)
unsigned_command = document(proposal.command)
# Review proposal.observation and its published evidence reference independently.
# Submit only after the normal current three-organization approval and signing process.
```

`collect` performs one bounded probe; it starts no polling loop. The operation must already have a
committed reservation. It checks original intent, executor revision, candidate identity, contract,
scope and artifact references. It retrieves and verifies complete base and patch bytes, applies the
patch without executing code, and checks remote Git bindings through `Drafts.inspect_patch`.

The exact repository, disabled destination Actions, unchanged base/head branches, head commit tree
and parent, and exact open draft identity must still match. Title, body, author, repository IDs and
commit IDs are checked. Missing, multiple, closed, edited or moved objects yield `unknown`, as do
unconfirmed provider replies. No lookup result, including absence, authorizes resending.

The original effect is checked again after the probe. Evidence is canonically encoded, published
through scoped storage and read back with digest/length verification. A final own-node read rejects
the proposal if its effect changed during publication. Publication failure returns no proposal.
An explicit later call can read again; it cannot dispatch or clear the original unknown claim.

## Governance and evidence

The returned `Proposal.command` contains only `kind=effect.reconcile` and its payload. It has no
request ID, nonce, signatures or execution permission. Administrators must review the evidence and
current effect, then create and sign the ordinary command using their current authority. An executor
alone cannot commit it. Consensus may classify a positive observation as `compensation_required`
if candidate evidence was withdrawn. Unknown observations retain an already known object number
and the full modeled charge; they never reset the reservation or refund the possible send.

The packaged `checkedflow schema effect-reconciliation` contract describes `Proposal.observation`.
It includes the original plan, prior effect classification/evidence/number and the own-node heights
before and after the probe. Heights bound local observations; they are not GitHub creation times.
The evidence reference is bound to the original intent and mission. It contains no token, host path,
exception text or raw provider response. It attributes an observation, not independent external truth.

Dispatch may remain disabled, the mission may be paused, the lease expired, the candidate withdrawn
or the original executor signing key revoked. These conditions must not prevent historical inspection.
They do prevent this read from granting fresh execution authority. Administrative approval is still
required, and the command is reevaluated against current consensus state when submitted.

## Limits and qualification

No read can atomically freeze GitHub and consensus through later human review or submission. A
published proposal may become stale immediately; compare its original identity and evidence with the
current effect before approving it. A stopped chain can still be inspected historically, but that
inspection does not establish fresh dispatch authority. Protected source/evidence and coordinator
journals must remain available. This method does not migrate old keys or restore lost journals.

The supported positive result is an exact, still-bound open draft. Changed branches, closed PRs and
human edits require separate operator investigation; automatic compensation, staging and effect
retirement remain incomplete. The source tests use signed consensus fixtures and exact GET replies,
including pause, expiry, revocation, withdrawal, absence, changed evidence and publication failures.
They do not qualify real multi-host operation or a live consensus-to-GitHub G3 run.
