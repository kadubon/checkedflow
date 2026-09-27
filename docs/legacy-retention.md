# Retain legacy migration evidence

`checkedflow.legacy_retention` connects authenticated legacy snapshots to the existing
[retention catalog](retention.md). It does not reinterpret old artifacts as new verified
capabilities. The full old snapshot preserves uncertain tasks, withdrawn capabilities and
residuals; their meaning remains governed by the old protocol.

## Operator sequence

1. Obtain the old `Checkpoint` independently from the snapshot. Authenticate the complete
   signed history and its coverage using the old replay path and trusted starting checkpoint.
   A history filename, digest or final snapshot alone does not prove complete history coverage.
2. Publish the reviewed history archive objects into a protected `RetentionStore`. Each
   `Reference` must have kind `archive`, the catalog's scope and a `manifest` field equal to
   the old checkpoint's state hash. References identify exact bytes, not signing authority.
3. Call `preserve(snapshot_bytes, checkpoint, history_references, catalog, access=policy)`.
   It authenticates the snapshot, reads every supplied archive, stores the canonical snapshot
   and creates a persistent replay pin covering the entire set. It returns `Retained`, containing
   the snapshot reference, archive references and `Pin`. Retain these values with the protected
   deployment inputs and catalog backup. No pin is silently released on failure.
4. Before cutover or recovery, call `verify(retained, checkpoint, catalog, access=policy)`.
   The existing pin must match its identity, sequence, principal and exact reference set.
   Every object is freshly read and digest checked. A missing/released pin or missing/corrupt
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
prove independent physical replicas, complete history coverage, remote archive provenance,
cross-host cutover or G1-G7 qualification.

The caller must authenticate history coverage and provision the retention policy independently.
This adapter does not derive missing history, fetch arbitrary references from candidates, bind a
new approval plan to unreviewed artifacts, or automatically release historical obligations after
an accounting adjustment. Startup/dispatch integration and deployed multi-host retention tests
remain required before operational release.
