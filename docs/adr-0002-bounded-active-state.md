# ADR 0002: Bounded active state and explicit request retirement

Decision date: 2026-09-26. Status: accepted design; implementation pending.

Use a bounded active set with authenticated append-only archive commitments and governed epochs.
Consensus state holds active records and the current archive commitment, not complete archived
bytes. Archive append batches bind previous root, ordered record identities and content hashes.
Historical materialization requires verified retained records anchored in the trusted commitment.
An unverified archive response cannot create authority or imply evidence is absent.

Commands bind their epoch. Requests from an older epoch receive `RETIRED_REQUEST`, never a fresh
dispatch or an invented duplicate-success response. Exact duplicate acknowledgment is bounded to
the active epoch. Actor nonces, fences, budgets, revocations and all unresolved obligations carry
across epochs. Key/actor counts are themselves bounded; new identities cannot reset allowances.

Only settled records without live dependants can leave the active set. Every live dependency and
unresolved effect remains a pinned active obligation across rollover. Archived artifacts cannot
be treated as currently reusable by lookup alone: reactivation needs explicit current authority
and fresh context/evidence checks. Cross-epoch live dependencies preserve their original identities,
withdrawal propagation and verification obligations. Rollover must reject if required carry-forward
records exceed the bounded successor envelope; it must not silently drop them.

Separate ordinary-work limits from administrative headroom. Admission reserves worst-case bytes
and record growth for expiration, invalidation, reconciliation and checkpoint operations, not only
the immediate command. Administrative commands still need ordinary quorum and valid signatures.
Failure to admit ordinary work must leave pause, drain, revocation and safe recovery possible.

The durable store atomically commits active state and archive append records. Snapshots bind both
commitments and staged imports cannot replace a working store until trust, sizes, chunks and state
hashes validate. Availability remains an external observation; missing bytes inhibit use without
changing committed acceptance. This ADR alone does not implement archival, snapshots or scaling.

## Settled-work retirement increment

The draft v2 `history.archive` command requires a paused mission, three-organization
administrative authority, an expected predecessor root and explicit ticket/task/candidate IDs.
Only records from retired request epochs qualify. Reserved or unknown funding and unfinished
or unknown tasks remain active. Retained tasks pin funding; retained candidates pin checks.
Candidates must be revoked or expired and have terminal, known checks. Accumulated charges and
verification allocations survive retirement and never enlarge the original mission budget.
A domain-separated chained digest commits each canonical batch; SQLite commits batch, block
and new head atomically. Readers need an independently trusted root. Historical objects do not
regain reuse authority. Pre-retirement v2 encodings omit zero extension fields, preserving their
hashes. The additive SQLite table requires upgraded readers; take an application backup before
an offline maintenance upgrade. This increment does not retire credentials or introduce the
future dependency/effect graph, remote archive availability or automatic archive scheduling.
