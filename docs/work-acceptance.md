# Funded verification and scoped acceptance

This v2 component distinguishes a completed attempt from acceptance of its artifact. A worker's
`task.finish` records an outcome and evidence digest. It does not make an artifact accepted.
Acceptance requires separate signed observations from verifier identities in different organizations.

The component operates on committed hashes and observations. It does not run candidate code,
download evidence, establish storage availability or infer that an arbitrary program is correct.
Use the [repository checker](repository-patch.md) for bounded independent output comparisons and
the [consensus adapter](operational-consensus.md) for ordering authenticated commands.

## Approval before checking

Three current administrative organizations authorize `artifact.admit`. Its payload binds:

- The current mission, the complete verification contract's SHA-256 digest (`target`) and the
  exact artifact digest (`artifact`). For a repository patch, the latter is the result-tree digest.
- An exclusive expiry height and four previously admitted, unstarted verification task IDs.
- One distinct organization for each verification task. All allowed workers for a check must
  be verifier identities in that organization and mission.

Each task already owns a reserved `verify` budget ticket for the same target. Checks cannot be
shared by multiple candidates. This narrow profile reserves four checks even though three passes
can establish acceptance. It preserves the remaining fourth check and its funding after early
acceptance; the state machine does not cancel it or refund its cost automatically.

The administrative signers and verification adapters must check the actual contract bytes against
`target`, and the artifact bytes against `artifact`. The pure core validates these bindings between
records; it cannot derive a full contract from a hash or establish that an uploaded object exists.
Changing a base commit, checker, dependency environment or use scope produces a different target.
This component does not authorize using the artifact under another target.

## Separate signed observations

After the approved check finishes, its owner submits `artifact.attest` with the candidate ID,
check ID, exact finished evidence digest and `pass`, `fail` or `unknown`. The owner must use the
same verifier key revision that owned the attempt. A task with an unknown outcome can only attest
`unknown`; missing evidence cannot be turned into a pass. A finished task may also report an unknown
verification verdict when its evidence is inconclusive.

The observation retains task, actor, key revision, organization, observation height, evidence and
verdict. Each distinct verdict is immutable. A contradictory verdict for the same completed check
is retained alongside the first and quarantines the candidate, even when neither verdict is `fail`.
There are at most three verdict records per check. Repeating the same verdict under a new request
cannot overwrite it. Exact duplicate command delivery remains idempotent through the request journal.

Consensus authenticates who asserted the verdict. It does not decide whether the verdict matches
external reality. In the repository adapter, a trusted evaluator compares bounded outputs outside
the candidate process. Shared evaluator defects remain a risk across organizations using that code.

## Reading status

The pure `checkedflow.core.work_acceptance.status(candidate, credentials, height)` function reads
a validated committed candidate and the corresponding current credential registry. It returns:

| Status | Meaning |
| --- | --- |
| `pending` | Fewer than three distinct organizations have passing observations. |
| `accepted` | At least three have passed; there is no adverse observation, revocation or expiry. |
| `quarantined` | A check failed or contradicted itself, an observation was withdrawn, or an observation key was revoked or is missing. |
| `expired` | The exclusive artifact expiry height has arrived, without a higher-priority adverse condition. |
| `revoked` | Three administrative organizations explicitly revoked the candidate. |

Explicit revocation takes precedence over quarantine; quarantine takes precedence over expiry.
All historical observations remain visible. An unknown fourth observation does not overturn three
passes, but still retains its evidence and full attempt cost. A failed fourth observation does
quarantine the candidate, even if a passing quorum was reached earlier.

**Accepted is not currently usable.** A consumer must additionally establish exact intended target,
current evidence/artifact availability, dependency validity, scope, execution authority and freshness.
The [repository reuse preparation](repository-reuse.md) now checks exact scope and available bytes
against a committed snapshot. The complete supervised reuse/availability service remains separate work. Never expose this
status alone as an authorization to execute, apply a patch or create a pull request.

## Withdrawal, retirement and compromise

The check owner can send `artifact.withdraw`, including after expiry or mission pause. Withdrawal
preserves the original observation and first withdrawal height. A later valid key revision of that
same verifier identity can withdraw historical evidence; it cannot rewrite or mint an old verdict.
`artifact.revoke` requires the administrative quorum and is irreversible for that candidate.

Routine key retirement does not erase observations valid at their original signing height.
Revocation is conservative: all observations under the revoked revision trigger quarantine,
including a key revoked as lost. The current registry does not distinguish a safe historical
cutoff for a lost key, so no such cutoff is inferred. A revoked key cannot submit new withdrawals;
governance can revoke the candidate, and key revocation already quarantines its historical evidence.

A malicious negative observation can reduce availability. The policy retains that
observation and blocks reliance on the candidate. It does not invent an automatic truth decision
or silently discard dissent. Recovery requires investigation and newly approved qualification;
there is no command to clear the historical negative in place.

## Bounds, portability and tests

The initial active/history limit is 64 candidates, four checks each, subject also to the existing
128-task and 128-budget-ticket bounds. These are finite retention limits, not a sustainable archive.
Full artifact lifecycle archival, dependency invalidation and residual reopening remain required
for the operational release. Candidate admission uses ordinary request capacity; administrator
revocation uses reserved administrative capacity. Verifier attestation/withdrawal uses ordinary
capacity, with governed journal rollover available when that capacity is exhausted.

The [command schema](../src/checkedflow/data/acceptance-command.schema.json) defines the `kind` and
`payload` portion placed inside the signed v2 envelope. The
[state schema](../src/checkedflow/data/operational-state.schema.json) defines retained candidates.
Strict decoding additionally validates task/credential references, funding targets, organizational
separation, observation times and canonical ordering. Schema validity alone is insufficient.

Source tests include signed replay, independent verdict-order expectations, a late negative after
quorum, preserved outstanding costs, conservative unknowns, withdrawal, expiry, key compromise,
routine retirement and forged snapshots. Fault injection removes quorum and negative-observation
checks to confirm those tests detect the errors. The installed-wheel four-node test performs
independent sandbox invocations and signed attestations under four laboratory verifier identities,
then withdraws one and checks quarantine and equal durable state hashes. Consult the
[implementation record](implementation-0.2.0.md) for observed results at a specific source revision.

Four laboratory identities and sandbox invocations on one hosted runner do not establish four
independent operators or hosts. This component alone does not qualify the complete G1–G7 release.
