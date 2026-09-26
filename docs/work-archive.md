# Retire completed work without resetting its cost

The draft v2 profile limits active funding tickets and tasks to 128 each, and candidates to 64.
Keeping every completed item active would eventually stop further work. `history.archive` moves
explicitly selected terminal records into authenticated local history, while carrying their costs
in the mission ledger. This is an administrative maintenance operation, not deletion or a budget reset.

## Operator sequence

1. Finish or explicitly cancel eligible work. Unknown results must remain unresolved.
2. Commit `mission.pause`, then `journal.rollover`. Record IDs from the current request epoch
   cannot be archived: their active duplicate receipts still refer to the admission records.
3. Inspect your validated full node's current state. Select eligible ticket, task and candidate IDs.
4. Submit a signed `history.archive` command with approval from three administrative organizations.
5. Check the committed outcome and archive head. If its predecessor changed, inspect state again;
   do not replace the expected root and blindly replay an old selection.
6. Resume only after the maintenance checks appropriate to the mission have completed.

Example command body, inside the usual authenticated v2 envelope:

```json
{
  "kind": "history.archive",
  "payload": {
    "mission": "example",
    "expected_root": "0000000000000000000000000000000000000000000000000000000000000000",
    "tickets": ["0:fund-check"],
    "tasks": ["0:check"],
    "candidates": ["0:result"]
  }
}
```

The example IDs must exist and satisfy the conditions below. Empty arrays are allowed, but the
whole selection cannot be empty. Selection ordering does not change batch ordering: records are
emitted in their existing canonical state order. Repeating the exact command within its active
request epoch is idempotent; retired-epoch resubmission is rejected as `RETIRED_REQUEST`.

## What stays active

| Record | Eligible for retirement | What prevents retirement |
|---|---|---|
| Funding ticket | `settled` or `released` | Reserved/unknown funding or a retained task referencing it |
| Task | `finished` or `cancelled` | Unfinished/unknown execution or a retained candidate referencing it |
| Candidate | Revoked or expired | A live candidate or any referenced check with unfinished/unknown execution |

All records must originate in an older request epoch. Candidate verdicts and execution outcomes
are distinct: a completed check can have an inconclusive verdict; its observations remain in the
archive. Unknown execution cannot be made terminal by archiving its candidate. Removing a task
does not require removing its funding in the same batch; retained funding can be selected later.

`archived_spent` accumulates retired charges; `archived_verification` accumulates their verification
charges. Total spent, available budget and protected verification capacity remain unchanged.
Released tickets contribute zero. Reserving new work still requires unused original mission budget.

## Portable commitment and persistence

The [command schema](../src/checkedflow/data/work-archive-command.schema.json),
[batch schema](../src/checkedflow/data/work-archive.schema.json) and
[state schema](../src/checkedflow/data/operational-state.schema.json) describe structure. The
[signed conformance vector](../src/checkedflow/data/work-archive-vector.json) fixes a complete
verification, revocation and retirement sequence. Runtime
checks additionally enforce authority, references, canonical bytes and accounting invariants.

Each batch body contains `chain`, `mission`, committed `height`, and the selected complete
`tickets`, `tasks` and `candidates` arrays. Encode it using the protocol's integer-only canonical
JSON rules. Sequence starts at one; the initial previous root is 32 zero bytes. The batch root is:

```text
SHA256(UTF8("CheckedFlow/work-archive/v1") || 0x00 ||
       hex_decode(previous_root) || uint64_big_endian(sequence) || canonical_body)
```

Sequence remains within the protocol's safe integer range. The body ceiling is 1,048,576 bytes.
The serialized wrapper contains `previous_root`, `sequence`, and parsed `body`; its ceiling is
1,048,832 bytes. Original evidence hashes, task fences and observations remain in each batch.
The state keeps only `history: {sequence, root}` plus cumulative accounting and retained records.
Default history and zero cumulative fields are omitted to preserve pre-retirement draft v2 hashes.

SQLite atomically writes batch, signed block journal and state. `Store.work_archive(sequence,
expected_root=...)` materializes a batch against a separately trusted commitment. The returned
content does not establish its own authority. `verify_history` replays commands and verifies every
generated archive, including the chain of predecessors. Application backup/restore regenerates
the same archive tables through replay.

## Upgrade and limits

Back up before an offline maintenance upgrade. The upgraded store adds `work_archives` to a prior
four-table v2 database without changing its state. Older code cannot open this profile afterwards;
do not mix binary versions across validators or reuse a database with older code. Coordinate a
common application upgrade before admitting the new command. This is not a rolling-upgrade protocol.
Preserving old state encodings does not guarantee replay of every earlier development journal: a
previous binary could have recorded `VERSION` for a command kind introduced later. Replay rejects
an outcome mismatch instead of silently changing that historical rejection. Such histories need an
explicit versioned migration design; this increment does not qualify that migration.

The command does not retire credentials, remove block journals, make archives available remotely,
or implement the future dependency/effect graph. Archived candidates cannot be used through the
current-state reuse API. Reuse would require new current authority and evidence. Quorum agreement
about an archive does not prove the correctness of the original work. Source tests and a real
infrastructure test definition cover this increment; only recorded executions count as evidence.
