# Retain legacy migration evidence

`checkedflow.legacy_retention` connects authenticated legacy snapshots to the existing
[retention catalog](retention.md). It does not reinterpret old artifacts as new verified
capabilities. The full old snapshot preserves uncertain tasks, withdrawn capabilities and
residuals; their meaning remains governed by the old protocol.

## Operator sequence

1. Obtain the old `Checkpoint` independently from the snapshot. Provision an independently trusted genesis
   checkpoint as well as the final checkpoint.
   A history filename, digest or final snapshot alone does not prove complete history coverage.
2. Publish the reviewed history archive objects into a protected `RetentionStore`. Each
   `Reference` must have kind `archive`, the catalog's scope and a `manifest` field equal to
   the old checkpoint's state hash. References identify exact bytes, not signing authority.
3. Call `preserve(snapshot_bytes, checkpoint, history_references, catalog, access=policy, initial=genesis_checkpoint)`.
   It authenticates the snapshot, reads every supplied archive, stores the canonical snapshot
   and creates a persistent replay pin covering the entire set. It returns `Retained`, containing
   the snapshot reference, archive references and `Pin`. Retain these values with the protected
   deployment inputs and catalog backup. No pin is silently released on failure.
4. Before cutover or recovery, call `verify(retained, checkpoint, catalog, access=policy, initial=genesis_checkpoint)`.
   The existing pin must match its identity, sequence, principal and exact reference set.
   Every object is freshly read and digest checked. The archive sequence is replayed from the
   independently trusted genesis, checking contiguous heights, original signatures and recorded
   outcomes, and must reach the exact final checkpoint. A missing/released pin or missing/corrupt
   object rejects verification. Verification never recreates the pin.

The root identity is `legacy-` followed by the old checkpoint hash. Repeating preservation
with the same bytes/references returns the same pin. Changing that root's references conflicts;
do not use a new identity merely to hide an incomplete migration record. Bounds allow one
snapshot plus one to 127 distinct archive references, subject to existing object/catalog quotas.
The controller's trusted recovery watermark remains mandatory when reopening its catalog.

`RetentionStore.verify_pin` verifies an existing root under the catalog's transaction lock,
excluding concurrent erasure while checking the supplied objects. This is a current observation;
it cannot promise future provider availability. Do not expose pin-release or provider-erasure
credentials to candidates, worker processes or untrusted remote clients.

## Guarantees and remaining integration

Tests use the published v1 fixture, actual stored archive bytes, durable SQLite roots, catalog
reopen, time advancement, explicit release and corruption. They show that pinned bytes remain
outside erasure plans and that verification detects missing roots or changed bytes. They do not
prove independent physical replicas, independent checkpoint provisioning, remote archive provenance,
cross-host cutover or G1-G7 qualification.

The caller must provision both trusted checkpoints and the retention policy independently.
This adapter does not derive missing history, fetch arbitrary references from candidates, bind a
new approval plan to unreviewed artifacts, or automatically release historical obligations after
an accounting adjustment. Startup admission now verifies the protected local catalog. Dispatch-time revalidation and deployed
multi-host retention tests remain required before operational release.


## Signed-history chunk contract

`checkedflow schema legacy-history` prints the portable schema. Each archive is JSON containing
exactly `initial` (the old state at that chunk's start) and `blocks` (one to 4096 contiguous old
block records). Use the original lowercase hexadecimal transaction bytes and recorded result
codes. There must be one to 127 ordered archive references, each within the artifact byte limit.
The first initial state must match the independently provisioned height-zero checkpoint; every
later initial state must match the previous chunk's replayed final state. The last block must
reach the independently provisioned final height and state hash. Empty chunks, missing suffixes,
extra blocks, reordered chunks, substituted starting states and differing outcomes fail closed.

`authenticate_history(history, initial, final, catalog, access=policy)` exposes the same bounded
replay check. `preserve` performs it before publishing the snapshot or creating a root; `verify`
repeats it against freshly read retained objects. A self-consistent history is not its own trust
anchor. No candidate code runs during replay. Large histories must be partitioned within the
explicit chunk/count limits; admission beyond those limits fails rather than dropping records.
This profile covers history from genesis; an arbitrary mid-chain checkpoint cannot substitute
for that starting point. Publication of chunks alone never enables new dispatch.


Archive references are protected operator inputs: authenticate their exact byte digests from the
old full node before publication. Replay and endpoint state hashes alone cannot prove historical
inclusion of rejected transactions or other state-neutral records. The retained catalog root
binds the reviewed archive digest set; `verify` checks that same set before replay. The standalone
replay helper assumes its reference list has equivalent independent provenance and does not
verify CometBFT block-commit proofs. Do not derive that trust from a submitted archive itself.


## Persisting the recovery handle

Serialize `retained.record()` with `checkedflow.wire.dumps`; restore it with
`legacy_retention.decode_retained`. The `legacy-retained` schema defines the portable JSON
shape. The decoder also checks checkpoint/scope binding and distinct snapshot/history digests.
It accepts at most 128 KiB, one snapshot and 127 ordered history references. Duplicate JSON
keys, unknown versions and boolean sequence numbers are rejected.

Keep this handle with the operator's protected recovery inventory. It contains references and
pin identity, not storage paths, credentials, independent checkpoints or permission grants.
Decoding succeeds even if storage has subsequently been removed or its pin released. Always
call `verify` against the independently configured catalog, current access policy, trusted genesis
and final checkpoint before using it. Verification never recreates missing pins. Updating the
handle alone cannot authorize a new archive set. Startup admission consumes this handle through a protected operator configuration. Dispatch-time
revalidation and deployed retention qualification remain separate requirements.


## Startup admission

Inherited v2 ABCI deployments require `--legacy-retention operator/retention.json` together with
all three succession files. Fresh genesis refuses succession/retention arguments. Every restart
verifies existing storage before creating the application database or opening a listener.
`checkedflow schema legacy-retention-local` describes the protected configuration:

| Field | Meaning |
| --- | --- |
| `version` | Exactly `checkedflow/legacy-retention-local/v1` |
| `catalog`, `objects` | Distinct existing SQLite files, relative to the configuration file or absolute |
| `namespace`, `scope`, `principal` | Original protected catalog and pin owner identities |
| `floor` | Independently retained positive catalog revision watermark |
| `initial` | Independently authenticated genesis checkpoint: chain, height zero, state_hash |
| `retained` | The reviewed recovery handle from `Retained.record()` |
| `policy` | Original retention_blocks, grace_blocks, object_limit and byte_limit |

Provision this file outside candidate-controlled workspaces, alongside authenticated root and
archive-digest records. Protect updates with the same operator review as the succession inputs.
Its maximum size is 256 KiB. The current startup backend uses `LocalStore` and `RetentionStore`;
provider bytes and catalog must use the configured capacity limits. The approved legacy snapshot
file must contain the exact canonical bytes retained in the catalog.

Missing or incomplete stores, a zero floor or catalog revision below the independently supplied
floor, mismatched owner/policy, released pins, missing bytes,
invalid replay or a different approved snapshot prevent startup. The verifier receives read-only
artifact permissions and does not repin or initialize missing inventories. Independently retain
current recovery watermarks: a caller-supplied old watermark cannot detect all rollback. These
checks do not establish physical replica independence, continuous availability after startup,
old-worker shutdown, validator transfer or complete cross-host migration.
