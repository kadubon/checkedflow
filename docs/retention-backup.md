# Backing up artifact retention records

Status: development component for the private retention catalog. This is not CometBFT state
sync, a consensus snapshot, a validator backup, or a complete operational recovery procedure.
The published 0.1.0 package does not contain this API.

## What must survive a storage incident

A byte-store backup alone is insufficient. Restored bytes could include an artifact that was
already deleted or whose last reader still needs protection. The retention catalog records
permanent tombstones, outstanding erasures, protected references and charged storage capacity.
Losing these records changes what the service is allowed to do.

`checkedflow.retention_backup.export_catalog` streams the catalog from one SQLite read transaction.
Concurrent source writes do not mix newer roots with older counters. The resulting checkpoint
binds the exact byte stream, controller namespace, scope, revision, height and record count.
It contains no local paths, provider endpoints, credentials or artifact contents. Root identities
and principals can still be sensitive; store exports privately.

The checkpoint is **metadata, not authentication**. An attacker can recompute a hash for a modified
backup. The operator must protect the checkpoint independently and retain the current revision
floor. Never obtain either trust input from the backup being assessed. Parsing a checkpoint with
`decode_checkpoint` does not make it trusted. This module deliberately has no trust-on-first-use
fallback, embedded public keys, self-issued signatures or automatic selection of the newest file.

## API and side effects

| Call | Required local permission | Effects |
|---|---|---|
| `export_catalog(store, output, access=...)` | `backup` for the catalog scope | Reads one catalog snapshot; writes the caller's binary stream |
| `decode_checkpoint(raw)` | None | Bounded metadata parsing only |
| `restore_catalog(source, destination, provider, checkpoint=..., current_revision=..., namespace=..., scope=..., access=...)` | `restore` for the expected scope | Reads a bounded stream; creates a new private directory and catalog |

Neither export nor restore performs provider I/O, executes candidate code, contacts another node,
changes acceptance, starts workers, stops an old controller or copies signing keys. `Access` must
be derived by trusted local policy, not deserialized from an API request. Restore creates a
controller around the supplied provider; it does not establish that provider's availability.

Export returns a checkpoint only after completing the stream. A failed export can leave partial
output; discard it. The caller owns the output stream and must flush, durably persist and protect
the data and checkpoint before announcing a successful backup. Default limits are 64 MiB and
100,000 records. Operators may explicitly raise these up to 1 GiB and 1,100,000 records. Exceeding
a bound fails; it never silently omits older tombstones. Streaming limits do not impose a wall-clock
deadline on a blocking caller-supplied stream; the supervising service must bound the operation.

Restore requires a positive independent `current_revision` and rejects a checkpoint below it.
It requires exact namespace and scope agreement. It validates ordering, shapes, reference integrity,
root ownership/fences, live root targets, configured capacities and recomputed accounting, then
checks the entire byte digest and length. Unknown erasures stay tombstoned and charged; erased
objects remain permanently retired. Restore cannot recreate erased physical bytes as live entries.

Only a new destination directory is accepted. Its parent is trusted operator configuration.
All import writes go into `pending.sqlite`. After validation and closing SQLite connections,
an atomic rename inside that directory creates `catalog.sqlite`. Existing directories, files and
links are never replaced. Ordinary exceptions remove only this call's fixed-name staging files.
A process crash before activation can leave a pending directory; it does not create an active
catalog. Inspect that directory and choose a new destination for another attempt. Do not rename
the pending file manually or reset a trusted floor to make an old backup open.

This guarantees application-level staged activation in the tested process-crash model. It does
not claim storage-device power-loss durability or coordinated restoration of all service stores.

## Recovery example

The following assumes an independently authenticated checkpoint and current floor, a stopped old
controller, a private parent directory, and an already configured provider. These prerequisites are
not inferred from the backup file. The API returns the restored controller; workers remain stopped
until the owning service has reconciled the rest of its state.

```python
from pathlib import Path
from checkedflow.retention_backup import restore_catalog

with Path("private-backup.jsonl").open("rb") as source:
    recovered = restore_catalog(
        source, Path("private-recovery/new-catalog"), provider,
        checkpoint=authenticated_checkpoint,
        current_revision=independently_retained_current_revision,
        namespace="operator-a", scope="mission-a", access=operator_access,
    )
```

Do not operate the old and restored controllers against the same provider namespace concurrently.
They would have different locks and could disagree about protected objects. A backup taken before
later catalog changes is insufficient for restoring their effects; it must fail against the later
current floor. Retain newer authenticated recovery material or reconcile through a separately
governed recovery procedure. This module cannot reconstruct lost later mutations.

## Portable stream contract

The profile is `checkedflow/retention-backup/v1`. Each record is one canonical CheckedFlow JSON
object, encoded in UTF-8, followed by exactly one LF byte. No BOM, CRLF, blank lines, extra fields,
duplicate JSON keys, floats or noncanonical whitespace are accepted. SHA-256 covers all bytes,
including every LF. Each line is at most 16,384 bytes. Counts include the header. Integers obey
the protocol's exact integer range; timestamps and Python objects do not appear.

The [checkpoint schema](../src/checkedflow/data/retention-checkpoint.schema.json) and
[empty-catalog vector](../src/checkedflow/data/retention-backup-vector.json) are packaged with the
distribution. The vector is test data, not a production checkpoint. Stream records are:

1. Exactly one header: `type`, `version`, `namespace`, `scope`, `revision`, `height`,
   `retention_blocks`, `grace_blocks`, `object_limit`, `byte_limit`, `objects`, `bytes`.
   `type` is `header`; the last two fields count non-erased objects and their declared plaintext
   lengths, including unknown erasures. They do not measure provider disk usage.
2. Object records, sorted by distinct lowercase SHA-256 digest: `type` (`object`), `reference`
   (the existing artifact-reference contract), `until` (retention height), `status`
   (`live`, `tombstoned`, or `erased`). Every reference has the header's scope.
3. Pin records, sorted by distinct identity: `type` (`pin`), `identity`, `sequence`, `principal`,
   `category`, `digests`. Categories are `pending`, `dependency`, `effect`, `snapshot`, `replay`.
   There are at most 4,096 pins; each contains 1–128 sorted distinct digests of live object records.
   Sequence numbers are positive and cannot exceed the catalog revision. Objects cannot follow pins.

Strings use Unicode scalar order for record sorting, consistent with the catalog's UTF-8 binary
ordering. Restore recomputes counters and rejects dangling or retired roots. Missing provider
bytes do not change catalog validity; fetch and independently verify them before actual use.

## Evidence and remaining work

[The tests](../tests/test_retention_backup.py) exercise real SQLite export/import, a concurrent
source write during export, root and accounting preservation, unknown erasure recovery, provider
resurrection denial, malformed and truncated input, byte/record limits, authorization before I/O,
old checkpoints, existing destination protection and a child process killed before activation.
Installed-package smoke checks exercise the portable vector; the separate real S3 fixture also
restores a tombstoned catalog against restored provider bytes. An individual test's inclusion is
not evidence that a given build passed; use its actual recorded qualification result.

Still required: protected checkpoint custody integrated with the current committed lifecycle,
coordinated catalog/provider/agent-journal backup, exclusive controller ownership after recovery,
replicated availability admission, consensus snapshot bootstrap, cross-host fault qualification,
and measured recovery objectives. A stale operator-supplied floor remains a stale trust input.
This component alone cannot authorize the 0.2.0 operational release.
