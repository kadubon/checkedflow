# Protecting artifacts and reclaiming storage

Status: a development service component. It is not wired to the complete v2 worker, dependency
graph, snapshot or effect lifecycle yet. The published 0.1.0 package does not contain it.

## Why hashes and deletion permissions are insufficient

An accepted patch can still disappear while a worker is using it. A snapshot may be needed for
recovery after it stops being the newest snapshot. An unresolved external action may need its
evidence indefinitely. Deleting such bytes because they are old would destroy obligations that
still exist.

`RetentionStore` wraps either the local or S3 provider in a private, durable SQLite catalog. The
catalog tracks storage consumption, minimum retention, protected roots and permanent tombstones.
It performs no candidate execution and changes no consensus verdict. A pin protects against this
controller's deletion; it does not prove provider availability or correctness.

All users of a provider namespace must use the same catalog/controller. A provider administrator
or another client bypassing the controller can still remove bytes. Use a separate bucket or prefix
and least-privilege provider credentials for each controller. Do not give candidates or ordinary
workers the raw provider, catalog file, maintenance policy or erase credentials.

## Authority and lifetime

`Access` is a trusted local policy result, not a bearer token or a client-supplied claim. A future
gateway must authenticate the caller and derive its current permissions. The direct SDK runs with
the operator's local filesystem authority; it does not expose an unsigned remote administration API.

| Permission | Operation |
|---|---|
| `read`, `write` | Verified retrieval or conditional publication through the catalog |
| `pin` | Create a named persistent root or release a root owned by the same principal |
| `maintain` | Advance trusted height and inspect a dry-run plan |
| `maintain` and `erase` | Apply an eligible plan or explicitly reconcile a pending erasure |

Authorization precedes catalog or provider I/O. Each catalog binds one scope and one operator
namespace. Its configured scope, retention/grace periods and quotas must match on reopen.

Publication verifies bytes and reserves capacity transactionally. Provider failure rolls back
catalog admission; an ambiguous remote write may leave an orphan at the provider. It does not
create a usable catalog entry or an automatic retry. A verified duplicate refreshes minimum
retention without consuming a second storage slot.

The owning service supplies a monotonic committed height through `advance`; no wall clock or
candidate timestamp drives reclamation. Defaults are 100 blocks of minimum retention plus 100
blocks of grace. A pin never expires automatically, including after a crash. Releasing it starts
at least a new full grace interval. Stalled work therefore retains storage until reconciled; the
controller does not trade evidence safety for automatic cleanup.

Roots have one of five explicit categories: `pending`, `dependency`, `effect`, `snapshot`, `replay`.
A root binds at most 128 objects. Multiple contracts can protect the same `(scope, digest)` bytes.
The controller does not parse dependencies from source code: the owning lifecycle must register
every required root **before admission/use**, then fetch and verify actual bytes. Missing required
roots are an integration error, not something a content hash can repair.

## Dry run and erasure

`plan` returns at most 128 eligible objects without modifying anything. Its portable
[schema](../src/checkedflow/data/retention-plan.schema.json) and
[vectors](../src/checkedflow/data/retention-vectors.json) describe scope, namespace, catalog revision
and typed references. Cross-reference scope and distinct physical digests are additional semantic
checks. A plan is not a signature or permission grant.

`sweep` requires current maintenance and erasure authority. It rejects a changed catalog revision
and rechecks every object's age and protected roots, even for a syntactically valid forged plan.
It commits all batch tombstones before invoking any provider erasure. New reads, writes and pins
then reject those identities permanently in this namespace.

Physical erasure follows separately. The local provider commits a scoped SQLite deletion. S3 makes
one signed DELETE and requires a subsequent 404 to confirm absence. No implicit DELETE loop runs.
An interrupted/denied/unconfirmed provider effect remains `unknown`; its tombstone and charged
storage capacity remain. `reconcile_erasure` is an explicitly authorized retry against the same
retired immutable identity. Once erasure is confirmed, capacity is released exactly once.

An `erased` record is the retained outcome of that attempt, not a promise that a provider
administrator can never restore the bytes later. Even if an old provider backup restores them,
the catalog rejects read, pin and republication. Verification revocation is another operation:
erasing bytes does not forge a failed checker vote, and a revoked artifact is not automatically
eligible for physical deletion.

## Reopening and rollback boundary

Creation requires `trusted_floor=0`. Reopening requires a positive trusted revision floor from an
independently retained recovery watermark. A missing catalog or a catalog older than that floor
is rejected. `revision(access=...)` exposes the current catalog revision for the owning recovery
system to bind to its authenticated checkpoint.

Never obtain the supposedly trusted floor from the backup being verified. A stale floor cannot
detect rollback past that stale point; this SDK parameter is not a complete bootstrap protocol.
The [catalog backup API](retention-backup.md) exports a consistent portable snapshot and restores
into a new staged directory against an independently authenticated checkpoint and current floor.
The operational recovery implementation must durably bind that revision and protect
its trust chain before this becomes a qualified deployment. Same-user modification of the
private SQLite database is outside the component's protection boundary.

## Minimal disposable example

```python
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from checkedflow.artifact_io import Access
from checkedflow.artifacts import LocalStore
from checkedflow.core.artifact import Reference
from checkedflow.retention import RetentionStore

# Fresh private demo directory; use an independent current watermark when reopening.
root = Path("private-retention-demo")
access = Access("demo-owner", frozenset({"demo"}),
                frozenset({"read", "write", "pin", "maintain", "erase"}))
store = RetentionStore(root / "catalog.sqlite", LocalStore(root / "bytes.sqlite"),
                       namespace="demo-operator", scope="demo", trusted_floor=0,
                       retention_blocks=2, grace_blocks=3)
body = b"example evidence"
ref = Reference("sha256", sha256(body).hexdigest(), len(body), "text/plain",
                "evidence", "demo", "1" * 64)  # Illustrative manifest only.
store.put(ref, BytesIO(body), access=access)
pin = store.pin("work-1", (ref,), category="pending", access=access)
assert store.get(ref, access=access) == body
store.advance(10, access=access)
assert not store.plan(access=access).objects
store.release(pin, access=access)  # Only after the owning operation is reconciled.
store.advance(13, access=access)
plan = store.plan(access=access)  # Reviewable dry run; still no deletion.
assert store.sweep(plan, access=access)[0].status == "erased"
```

Defaults bound active storage to 4,096 objects and 256 MiB, and active roots to 4,096. Tombstones
remain in indexed durable storage; they are not copied into every operation or automatically
evicted. Operators must budget disk for that retained history. Batch and root limits stay fixed.
These are storage bounds, not a claim that the unfinished consensus lifecycle passes longevity.
Byte accounting uses declared verified plaintext lengths, not measured disk usage, replicas,
provider versions or database overhead. Out-of-band restoration can consume provider space even
when catalog admission remains denied; provider disk quotas and recovery reconciliation remain
operator responsibilities.

Writes, reads and erasure serialize through a SQLite write reservation. A WAL read transaction
alone would not exclude a concurrent erasure. Provider calls can hold the reservation; competing
catalog operations have a ten-second SQLite wait. A worker supervisor still needs to bound total
attempt time and classify I/O failures conservatively. Retention does not supply that supervisor.

## Validation and remaining integration

[Tests](../tests/test_retention.py) cover actual SQLite persistence, dry-run immutability, protected
root categories, stale plans, forged eligible lists, grace boundaries, concurrent quota admission,
pin fencing, provider resurrection, independent revision floors and a killed child process after
tombstoning but before erasure. Fault injection checks the key guards. The real S3 fixture exercises
the same controller, scoped deletion and rejection after provider-byte restoration.

Remaining work includes deriving roots and trusted heights from the complete committed lifecycle,
replicated availability admission, governed maintenance authority, protected checkpoint custody,
cross-host recovery and full operational qualification. Until those integrations pass, do not
describe this component as a complete retention service or enable unattended operational GC.
