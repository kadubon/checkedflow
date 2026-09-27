# Legacy migration inventory

The development SDK can inspect a complete v1 snapshot against a separately trusted
checkpoint. This is a read-only prerequisite for migration, **not an implemented successor
deployment or permission to resume work**. The maintenance-window cutover, inherited v2
accounting, reconciliation, validator custody and mixed-version recovery remain unfinished.

## Why preserve more than the available budget?

Suppose a task reserves 10 units and its worker disappears. V1 eventually charges the full
10-unit ceiling and records an uncertain outcome. Its reservation is now zero, but the
outstanding investigation remains. Copying only reservations would lose that obligation;
charging it again in a successor would count the same attempt twice. Likewise, a revoked
dependency cannot become usable simply because a new chain starts at height zero.

`checkedflow.legacy_inventory.inspect_snapshot(raw, trusted)` returns an immutable inventory:

- The complete canonical snapshot, retaining all missions, tasks, capabilities, dependency
  edges, residual reasons/resolutions, keys, nonces, request digests and fence numbers.
- Per-mission original budget, spent ceiling charges, reservations and available balance.
- Sorted identities for pending tasks, open residuals and withdrawn capabilities.

An uncertain task appears in the pending list even when its cost was already charged.
Ready tasks are also pending; the list does not mean every entry has executed. The function
does not advance block height, expire a lease, release funds, run code or change v1 semantics.
The summaries help reviewers locate obligations; retain the full snapshot and signed history.

## Trust and bounded input

Construct `Checkpoint(chain, height, state_hash)` from an independently authenticated old
full node or an operator-provisioned checkpoint. Never copy these values from an untrusted
snapshot and then describe the comparison as authentication. A digest supplied alongside
its own document proves no independent trust.

The SDK rejects input larger than 4 MiB before parsing. Existing strict JSON parsing rejects
duplicate keys, floats and out-of-range integers. The canonical state hash, chain and height
must match the supplied anchor. Typed decoding must retain every field. Reservations must
equal the sum of funded task ceilings for each mission; spent plus reserved cannot exceed
the original budget. A task without its mission is rejected. An inconsistent operator-anchored
snapshot is an investigation, not an opportunity to repair history silently.

The API accepts bytes and performs no I/O. Callers reading a file or network stream must bound
that read too. The checkpoint is a Python SDK record, not a signed migration manifest or a new
wire protocol. The returned bytes use the original v1 canonicalization contract.

## Remaining cutover requirements

Before enabling a successor, the implementation must bind its genesis to old authority and
the frozen checkpoint, preserve outstanding balances without new allowances, retain the
authenticated history and artifacts, stop old dispatch, and establish exclusive validator
signer ownership. Missing evidence must remain unresolved. Migration cannot manufacture
verification signatures or translate old commands into new execution authority.

This inventory does not prove that a supplied old checkpoint is the latest one, that a
validator has stopped, or that artifact storage is complete. It does not contain a cleanup,
activation or balance-reset operation. Keep snapshots protected: candidate source and result
fields may contain application data even though signing private keys are not state fields.

## Verification

`tests/test_legacy_inventory.py` inspects every replayed block of the frozen published
0.1.0 fixture, including funded work, charged unknown outcomes and revoked dependencies.
It checks independent checkpoint mismatches, malformed input, inconsistent accounting and
lossless preservation. The fixture's existing hash and checkpoint expectations remain fixed.
These source tests do not qualify an installed multi-node migration or G4/G6.

See [the original capture](legacy-capture-0.1.md), [the protocol decision](adr-0001-versioned-operational-state.md)
and [the implementation record](implementation-0.2.0.md).
