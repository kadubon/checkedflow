# Legacy migration inventory

The development SDK can inspect a complete v1 snapshot against a separately trusted
checkpoint and prepare a paused v2 genesis with conserved funding. These are migration
components, **not a qualified successor deployment or permission to resume work**. Governed inherited-obligation reconciliation and retention admission are implemented components.
The complete maintenance-window cutover, cross-host validator custody and mixed-version
recovery remain unqualified.

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

### Preparing conserved successor funding

`checkedflow.legacy_successor.prepare(raw, trusted, initial, mission="old-mission")` accepts
the original snapshot, its independent checkpoint and a pristine paused v2 genesis. It
returns a paused successor for one explicitly selected old mission. The successor chain
must differ from the old chain. Existing tasks, receipts, configured funding or a running
mode in `initial` are rejected. Other old missions are retained in the committed snapshot;
this function does not create a deployment mapping for them or distribute their allowance.

The successor budget contains an `inheritance` record with old chain, mission, height, state
hash, original budget, spent and reserved values. The hash commits the full original snapshot,
including fences, nonces, dependencies and residuals. Retain those bytes and signed history
under protected storage. This reference alone neither proves storage availability nor pins
objects against deletion; operational retention and governance still need integration.

The runtime counts inherited spent units exactly once and keeps inherited reservations held
separately from new tickets. Ordinary settlement, request rollover and settled-work retirement
cannot release them. An already charged unknown attempt is not charged again. No old task is
made executable, and no old artifact becomes a new accepted candidate. New tasks use the
new chain's signatures and identities; old signed commands are rejected.

The successor protects the smaller of the original verification reserve and the remaining
available balance for new verification. It does not infer old verification-phase charges
from incomplete final task records or invent metering. An exhausted inherited budget may
have zero protected balance and permits no new reservation. The original verification policy
remains in the historical snapshot. This explicit v2 allocation must be included in cutover review.

The actual SQLite store and signed-history backup/replay accept this prepared baseline while
rejecting preloaded new work. Genesis trust is still independently provisioned: storing or
replaying a self-consistent genesis does not prove that old or new administrators approved it.
The SDK creates no validator configuration, signer state, listeners or external operations.

`legacy-successor-vector.json` ships the exact old checkpoint, public test genesis, prepared
state, expected hash and balances for ports. `operational-state.schema.json` defines the
optional inheritance object. When absent, serialization omits it, preserving earlier v2 hashes
as well as all v1 hashes. Older development writers reject the new field and must not join a
successor deployment. This is not a claim of rolling-upgrade compatibility for those writers.

### Activation and reconciliation still required

[Succession approvals](succession-approval.md) now bind both administrative quorums to the
exact old checkpoint, prepared successor and validator mapping. Verification does not establish
old-node shutdown or exclusive custody; the complete cutover remains unqualified.

Before enabling a successor, the implementation must bind its prepared genesis to old authority
and the frozen checkpoint, qualify outstanding-balance conservation, retain the
authenticated history and artifacts, stop old dispatch, and establish exclusive validator
signer ownership. Missing evidence must remain unresolved. Migration cannot manufacture
verification signatures or translate old commands into new execution authority.

This inventory does not prove that a supplied old checkpoint is the latest one, that a
validator has stopped, or that artifact storage is complete. It does not contain a cleanup,
activation or balance-reset operation. Inherited reservations remain locked until a separate
governed reconciliation lifecycle is implemented and qualified. Keep snapshots protected: candidate source and result
fields may contain application data even though signing private keys are not state fields.

## Verification

`tests/test_legacy_inventory.py` inspects every replayed block of the frozen published
0.1.0 fixture, including funded work, charged unknown outcomes and revoked dependencies.
It checks independent checkpoint mismatches, malformed input, inconsistent accounting and
lossless preservation. The fixture's existing hash and checkpoint expectations remain fixed.
These source tests do not qualify an installed multi-node migration or G4/G6.

`tests/test_legacy_successor.py` also exercises actual signed new work, held legacy reservations,
budget exhaustion, old-command rejection, pristine-genesis checks, SQLite replay, backup
restoration and the portable state vector. No physical node cutover is claimed by these tests.

The required infrastructure suite now includes
`test_v2_inherited_accounting_commits_and_recovers_on_four_nodes`: a prepared baseline, signed
new work, journal rollover, one process crash/restart, common-height hash comparison and durable
replay of all four application stores. The earlier published-fixture case passed CI at revision 797acbd; the current retention
admission and live-old-chain extensions require their own execution. Even a pass
will qualify successor accounting on four local CometBFT processes, not physical host replacement,
old-validator retirement, independent organizations or the complete G4/G6 migration path.

See [the original capture](legacy-capture-0.1.md), [the protocol decision](adr-0001-versioned-operational-state.md)
and [the implementation record](implementation-0.2.0.md).

[Inherited obligation reconciliation](legacy-reconciliation.md) now provides separate governed
adjustment of prepared legacy records. It does not create new executable tasks or release
funding through ordinary ticket settlement.

The [legacy retention adapter](legacy-retention.md) pins an authenticated snapshot and reviewed
history references. It also authenticates ordered history by replay between independent roots. Physical replica
independence and continuous deployment custody remain separate requirements.


## Live-old-chain qualification case

The inherited four-node case has two required variants: the frozen published capture and a live
legacy laboratory. The live variant first performs actual generation, independent gVisor checks,
registration and reuse on four old CometBFT nodes. It then leaves an unreported leased attempt
to expire, revokes a capability and its dependents, and revokes all old worker credentials. A new
worker request must be rejected before stopping every owned old application/consensus process.

Only after process termination does the case read the four durable histories. Each replay must
reach the same independently observed committed AppHash. That preserved root and the old
administrators' actual laboratory keys are used for successor approval; the new validator keys
and chain are separate. All four successor nodes reopen their own retained copies, preserve old
charges and unknown obligations, execute new signed accounting, restart one node and compare
replay. Releasing one retention pin must inhibit that node's dispatch watchdog.

The case is registered but its current live variant has not yet been executed. It does not claim
four separate host environments, independently managed organizations, physical signer transfer,
external-effect cancellation or protection from an administrator restarting the retired chain.
Production cutover still requires protected restart fencing and the four-host G6 evidence. Keep
old and new credentials separate and recover forward after the first new commitment.
