# Administrative approval of succession

`checkedflow.succession` binds a proposed move to the exact independently trusted old
checkpoint, prepared new state and configured validator keys. Verification requires three
distinct old organizations **and** three distinct new organizations. One side's quorum cannot
replace the other. These approvals do not establish that old dispatch stopped, that a signer
has exclusive ownership, or that all signing hosts share a non-rollbackable approval record. Those custody and
activation controls remain unfinished and mandatory before operational release.

## Review, sign, verify

1. Retain the complete old snapshot and history. Obtain `Checkpoint` independently of the
   supplied snapshot. Prepare the paused successor using [legacy preparation](legacy-migration.md).
2. Call `proposal(legacy, trusted, successor, validators)`. The validator input is a sorted tuple
   of four `(organization, public_key)` pairs. Each key must be distinct and separate from old
   application keys and new command credentials. The function recomputes successor accounting
   from the authenticated old state; caller-supplied budget modifications are rejected.
3. Have each administrator explicitly review the old mission, checkpoint, new chain, full
   prepared state and validator set. `approve(plan, side, organization, identity, signer)` signs
   through the supplied protected signer. It performs no automatic policy approval. Never expose
   this signing operation to a candidate or use it to sign competing plans for one old mission.
4. Assemble `{ "plan": plan, "approvals": [...] }` and call `verify(raw, legacy=...,
   trusted=..., successor=..., validators=...)` with the independently protected expected inputs.
   The returned `Approved` record contains the plan digest and the distinct organizations on
   each side. It is evidence of administrative approval, not a validator startup instruction.

Old signatures are verified against organization keys in the authenticated old state. New
signatures require administrator credentials in the prepared genesis, usable at height zero.
Worker keys and identity/organization substitutions are rejected. Every supplied signature must
pass, including extra signatures beyond the two quorums. Duplicate organization votes on one
side are rejected. Manifest ordering does not change the approved plan digest.

## Portable contract

`checkedflow schema succession` prints the packaged JSON Schema. The portable
`succession-vector.json` contains public test identities, original checkpoint, successor,
validator mapping, six actual signatures and expected plan hash. Neither file supplies an
operator's production trust anchor or credentials.

The signing domain is the ASCII bytes `CheckedFlow/succession-approval/v1` followed by one zero
byte. Append RFC 8785 canonical JSON for `{plan, side, organization, identity}`. Sign the result
with Ed25519. Encode signatures as 128 lowercase hexadecimal characters. Keys and state hashes
use 64 lowercase hexadecimal characters. `plan.profile` is `checkedflow/succession/v1`.

The plan binds old chain/mission/height/state hash, new chain/mission/state hash and all four
validator organization/key pairs. The plan hash is SHA-256 of the canonical plan alone. Expected
plan equality is checked before accepting any signature; a manifest cannot select its own
trusted checkpoint, new state or validator set. Admission permits at most 16 KiB, eight
approval entries and an 8 KiB signing message. Existing lexical admission rejects duplicate
JSON keys and non-interoperable numbers. Unknown fields fail closed.

Stable failures include `BINDING` for an unexpected state/plan, `AUTHORITY` for the wrong
administrator, `SIGNATURE` for invalid/duplicate votes, `QUORUM` for insufficient organizations,
`VERSION` for the wrong purpose/profile and `LIMIT` for oversized input. Rejected approval is
not permission to revise historical charges, retry an external effect or bypass operator review.

## Scope of evidence

Tests use the immutable published-0.1.0 fixture, real Ed25519 signatures and a prepared v2 state.
They check independent quorums, role substitution, validator/state tampering, duplicate votes,
invalid extra signatures, bounds and the portable vector. They do not prove physical shutdown,
exclusive cross-host signer custody, non-equivocation, managed-signing deployment or G4/G6.

## Durable approval custody

`checkedflow.succession_journal.ApprovalJournal` protects one old chain/mission and one
administrator identity. Provision it once with `create=True`, then reopen the same protected
path with the same `chain`, `mission`, `side`, `organization` and `identity`. Exclusive creation
rejects existing files; ordinary reopen never creates a missing database. Partial initialization,
missing identity rows and foreign tables require recovery instead of silent initialization.

After independently reviewing the `proposal(...)` output, call `journal.sign(plan, signer)`.
The adapter commits the canonical plan with SQLite FULL durability before calling the signer.
A competing plan is rejected before signing, including one with a different old checkpoint.
Concurrent connections share the same exclusion. A signer exception or process death retains
the claim. The identical plan may be signed again; this does not authorize execution or cutover.
Close the connection with `journal.close()` when finished.

The journal contains no private key and does not independently approve a proposal. Keep it in
protected operator storage beside signer custody, outside candidate workspaces. Never delete,
reinitialize, restore an older copy, or create another journal to bypass a conflict. A single
SQLite file does not prevent a second host with a copied key from signing: cross-host ownership,
backup rollback protection and signer policy must enforce that separately. Direct `approve(...)`
remains a low-level primitive for implementations with an equivalent protected signing policy.
No automatic conflicting-plan reset is provided.

Source tests cover competing connections and actual process termination inside the signer,
then reopen the same journal and verify a conflicting plan is rejected. These observations
establish local durable exclusion, not cross-host fencing or a qualified migration deployment.


## Startup admission

The v2 ABCI service refuses inherited genesis unless all three operator-provisioned files are
provided. Use the existing configuration, database and loopback address arguments together with:

```sh
python -m checkedflow.distributed.operational_application \
  --configuration operator/operational.json --database node/operational.sqlite \
  --address 127.0.0.1:26658 \
  --succession-manifest operator/approval.json \
  --legacy-snapshot operator/legacy.json \
  --legacy-checkpoint operator/checkpoint.json
```

The checkpoint file is exactly `{ "chain": "old-chain", "height": 123,
"state_hash": "<64 lowercase hexadecimal characters>" }`. Provision it from the authenticated
old full node through an independent operator channel. Copying checkpoint fields from the
submitted manifest does not establish trust. The snapshot and manifest remain subject to
signature, accounting, state-hash and validator binding checks. CLI reads are bounded to 2 KiB
for the checkpoint, 16 KiB for the manifest and 4 MiB for the snapshot.

Every restart repeats verification against the configured initial state before opening the
application database or listening socket. A fresh, non-inherited genesis rejects succession
inputs instead of silently ignoring them. Keep these files and the initial configuration in
protected recovery storage. They are not candidate-controlled inputs and contain no private keys.

Python embedders call `serve(..., succession=Succession(manifest, legacy, trusted_checkpoint))`.
The lower-level `Application` class is a consensus adapter, not a deployment admission service;
embedders that construct it directly must enforce equivalent startup admission. Neither path
stops the old deployment or demonstrates exclusive physical ownership of validator keys. Complete
those maintenance-window controls before enabling dispatch. The four-node laboratory case is configured to exercise
approved startup/restart but remains a single-operator test, not G6 qualification.
