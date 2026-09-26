# Atomic control-state and archive persistence

Status: **IMPLEMENTED; SOURCE TESTS EXECUTED**. This stores the initial v2 control profile only.
It is not the full operational artifact service, a CometBFT adapter, state-sync implementation,
backup/restore system or complete work-state store. Those integrations remain release requirements.

## What commits atomically

`checkedflow.operational_storage.Store` owns an operator-provided SQLite path and a pinned
initial control state. Use a private, operator-controlled directory; do not open a downloaded or
candidate-supplied SQLite file. POSIX creation mode alone is not a Windows ACL. This adapter does
not promise filesystem race protection against another process controlling that directory.

The store enables WAL and FULL synchronous mode. A serialized write transaction reads the current
state, verifies the expected predecessor hash and contiguous block height, authenticates each raw
command and derives its transition. It then commits all three together:

- The resulting bounded current-state record and application hash.
- Original transaction bytes, rejection/acceptance outcomes and block hash-chain record.
- Every request-epoch archive emitted by rollover in that block.

No caller-provided next-state object bypasses the signed transition. Failed commands retain their
recorded rejection code and leave the explicit block-height advancement intact. Storage failures
abort the entire SQL transaction; they cannot be recorded as successful command observations.
Replay of the same already-committed block is acknowledged without applying its commands again.
A conflicting block at that height, a gap or a stale predecessor is rejected.

This acknowledgment applies to storage/consensus block replay. It is not permission to submit a
retired command as fresh work: a new block containing an old-epoch request still receives
`RETIRED_REQUEST`. This store does not choose distributed ordering. A future CometBFT adapter
must bind preparation, finalization and durable commit to the same application-state commitment.

## Integrity, history and trust

The store binds its exact genesis bytes, including mission, organizations, credentials, limits and
profile. A v1 or unrelated database is rejected. Startup validates the current state and the latest
referenced archive. Missing/corrupt bytes stop local use; they do not rewrite committed acceptance.

`archive(epoch, expected_root=...)` retrieves one epoch and verifies its commitment against the
root supplied by the caller. `verify_history(expected_hash=...)` streams blocks from pinned genesis,
re-authenticates signatures, reproduces rejection codes and state hashes, checks archived batches
and compares the final state to an independently trusted checkpoint. History grows on disk, while
only bounded active state is replayed in memory. Normal block commits do not replay all history.

These checks establish application-history consistency relative to the supplied anchor. They do
not authenticate a remote validator set, prove CometBFT finality or turn a self-supplied hash into
bootstrap trust. They must not be exposed as arbitrary untrusted snapshot import.

The [state schema](../src/checkedflow/data/operational-state.schema.json) and
[archive schema](../src/checkedflow/data/request-archive.schema.json) are structural contracts.
`operational_codec` additionally enforces nonce continuity, fixed identity slots, mode/profile,
receipt-class capacities, ordering and byte ceilings. A decoded structure alone grants no authority.
Both state and an individual archive have a 4 MiB encoded ceiling. Blocks have at most 256
transactions, 1 MiB per original transaction and 2 MiB total original bytes. Hex journal encoding
has a separate bounded expansion allowance. No clock or I/O is introduced into the pure core.

## Executed and pending recovery checks

Source tests exercise reopen and authenticated replay, rejection retention, atomic rollback after
archive insertion, concurrent conflicting commits, tampering, missing archive data and actual
child-process termination before/after commit. The pre-commit crash uses a small SQLite cache to
exercise uncommitted page spill. The post-commit crash loses the reply; reopening and repeating
the same block preserves a single committed rollover. These are process-crash tests, not evidence
against power loss, dishonest disks or independent validator failures.

Consistent backup including SQLite WAL, staged restore, authenticated snapshot chunks, CometBFT
catch-up, validator signer ownership, A2A callback configuration and full mission liabilities are
still pending. Do not copy only the live main database file as a backup. Do not restore consensus
signer state into two active nodes. See the [implementation record](implementation-0.2.0.md).
