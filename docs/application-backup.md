# Application history backup and restoration

The v2 application database can now be exported as bounded canonical JSONL and restored by
replaying its original signed transactions into a new private directory. Replay reconstructs
state and request archives; it retains rejected bytes and their rejection codes. A backup does
not provide its own authority: the operator must supply an independently trusted checkpoint,
the original trusted genesis and a current height floor.

This is an **application-only recovery component**. It does not restore CometBFT block stores,
consensus WAL, validator signing state, artifact/provider bytes, A2A callback journals, external
effects or secret custody. It does not start services or establish caught-up readiness. Those
coordinated recovery procedures remain prerequisites for the operational release.

For a v1 deployment, [legacy inventory](legacy-migration.md) provides a separate read-only
checkpoint inspection. It is not accepted as a v2 application backup and cannot activate a
successor. Keep the old signed history and unresolved obligations during migration planning.

## Trust inputs and format

`Checkpoint` binds chain, mission, genesis application hash, final application height and hash,
exact backup SHA-256 and byte length. The packaged
[checkpoint schema](../src/checkedflow/data/application-checkpoint.schema.json) defines its JSON
shape; the [public vector](../src/checkedflow/data/application-backup-vector.json) supplies a
genesis, rejected transaction, signed rollover, canonical backup and expected checkpoint.
Parsing with `decode_checkpoint` checks metadata, not provenance, quorum or freshness.

Record zero is the canonical object `{version, chain, mission, genesis_hash}` followed by LF.
The version is `checkedflow/application-backup/v1`. Each following LF-terminated record is the
existing application block `{height, previous_hash, state_hash, transactions}`. Transaction entries
retain `{raw, code}`; `raw` is lowercase hexadecimal of the original envelope bytes, including
malformed/rejected inputs. Heights start at one and are contiguous. No blank records, duplicate
JSON keys, noncanonical records, unknown block fields, missing final LF or trailing data are allowed.
All JSON uses the project's integer, Unicode and canonicalization rules.

The checkpoint height counts application blocks, excluding the header. The hash is the committed
application state **at that height**, not an unqualified CometBFT block-header AppHash. No light
client proof is supplied or implied. A later block header's AppHash relationship must be verified
by the consensus adapter before converting a consensus trust source to this application checkpoint.

Exports default to 64 MiB and 100,000 blocks. The absolute limits are 1 GiB and 1,000,000 blocks;
each line is bounded independently. Set smaller limits for the operator's declared workload.
The backup includes complete retained history rather than a constant-time state snapshot. Replay
cost grows with history. This does not solve sustainable state archival or selective materialization.

## Export and restore

The base installation includes three offline commands:

```sh
checkedflow application-backup export --genesis trusted/genesis.json --database operator-data/application.sqlite --expected-hash TRUSTED_APPLICATION_HASH --destination operator-backups/new-export
checkedflow application-backup inspect-checkpoint --checkpoint trusted/checkpoint.json
checkedflow application-backup restore --genesis trusted/genesis.json --source operator-backups/new-export/history.jsonl --checkpoint trusted/checkpoint.json --current-height 123 --destination operator-data/new-restoration
```

Replace the hash and height with independently retained values for the actual deployment.
`--genesis` takes an initial v2 application state, not the ABCI wrapper or CometBFT genesis.
The [machine-readable command catalogue](../src/checkedflow/data/application-recovery.json)
lists inputs, writes and authority boundaries. All three commands have no network or external
provider effects. Export writes `history.jsonl` and `checkpoint.json` into a new directory;
`--byte-limit` and `--block-limit` lower its bounds. A failed export may leave a partial file,
but does not produce a completion checkpoint. The emitted checkpoint still needs independent
protected custody. Inspection parses metadata only and explicitly reports both authentication
and backup verification as false. Restoration does not start a validator or claim readiness.

Use private operator-owned directories and files. The parent directory is trusted configuration,
not a path supplied by a worker. A caller must authorize backup/restore access separately; this
offline SDK does not accept remote requests or infer permission from possession of a checkpoint.

```python
from pathlib import Path
from checkedflow.operational_backup import export_history, restore_history
from checkedflow.operational_storage import Store

# initial, trusted_application_hash, checkpoint and current_height come from
# independently protected operator records, not a worker or the backup itself.
store = Store(Path("operator-data/application.sqlite"), initial)
with Path("operator-backups/history.jsonl").open("xb") as output:
    exported = export_history(store, output, expected_hash=trusted_application_hash)
# Flush/fsync the backup and preserve exported in protected checkpoint custody
# before treating this export as a recovery source.

with Path("operator-backups/history.jsonl").open("rb") as source:
    restored = restore_history(
        source, Path("operator-data/restored-application"),
        initial=initial, checkpoint=checkpoint, current_height=current_height,
    )
```

Export uses one SQLite WAL read transaction and verifies signatures, outcomes, predecessor hashes,
stored epoch archives and the final state while streaming. Concurrent later commits are outside
that read snapshot. Unsigned journal-container whitespace is canonicalized while the embedded
original transaction bytes remain unchanged. A short write, ceiling violation or verification failure raises instead of
returning a checkpoint; the caller must treat any partial output as incomplete. The optional
`Store.verify_history(..., consume=callback)` callback likewise receives provisional block records:
only successful return establishes completion of the whole verification.

Restore rejects a checkpoint older than the independently supplied floor before creating a
destination. It exclusively creates a new directory, replays into `pending.sqlite`, compares every
outcome and state hash, verifies the complete backup bytes, checkpoints SQLite, syncs the pending
database and renames it to `application.sqlite`. Existing directories are never overwritten.
All content checks precede activation. On failure the inactive directory is preserved for operator
inspection; automatic retries do not erase it or reset state. After an ambiguous process exit,
inspect the fixed active filename and independently reverify it before any further action.

Filesystem parent-directory durability, backup publication/custody and full power-loss behavior
remain operator/platform obligations. Process-exit tests do not simulate a physical disk losing
power. Do not claim that this function makes a multi-service deployment recoverable by itself.

## Rejection and recovery rules

| Condition | Result / operator action |
|---|---|
| Wrong genesis, scope, bytes or final checkpoint | `BINDING`; recover the independently trusted records |
| Checkpoint below the current floor | `RESTORE`; obtain a current backup; never lower the floor to force acceptance |
| Truncated, noncanonical or extra records | `SHAPE` / `LIMIT`; retain the failed input and obtain complete bytes |
| Gap or replay disagreement | `HEIGHT` / `REPLAY`; investigate history integrity; do not edit recorded outcomes |
| Existing destination | Local filesystem error; inspect it; never overwrite a live database |
| I/O failure or interrupted staging | Preserve the inactive directory; inspect storage before a new restoration |

Do not reset unknown costs, nonces, epochs, leases, key revisions or revocations after restoration.
Do not copy a validator's signing state into a second active process. A restored application hash
does not grant signer ownership, external-effect authority, artifact availability or permission to
resume work. Establish those through the complete deployment recovery procedure.

## Test scope

Dedicated source tests cover real SQLite replay, regenerated archives, retained uncertain costs,
verification reserves, concurrent WAL writes, corrupt/truncated/extra records, altered checkpoints,
rollback floors, short output, ceilings, existing destinations, persistence failures and actual
process exit before/after activation. The portable vector is checked against its schema and
round-trips byte-for-byte. Selected mutations remove floor and replay checks; they must be detected.

Installed-package checks exercise restoration separately from the source checkout. The real
CometBFT/gVisor integration case also exports and restores each stopped node's actual application
history after verification withdrawal and quarantine. That case needs a successful exact-source
infrastructure run; adding the case does not constitute a passing result or full G6 qualification.
