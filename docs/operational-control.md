# Initial v2 control and request accounting

Status: **IMPLEMENTED, SOURCE-TESTED**. This is an unreleased SDK control
profile, not the complete operational runtime. A [separate v2 consensus adapter](operational-consensus.md)
now connects it to CometBFT. The [bounded worker SDK](worker-supervision.md) connects isolated
execution, and [native v2 gateways](operational-agents.md) expose signed control through A2A/MCP.
Complete unattended services and state sync remain pending. The bounded
[task ownership extension](work-tasks.md) now accepts isolated-task commands, but does not
dispatch candidate code or establish artifact acceptance.
The separate [work acceptance extension](work-acceptance.md) records funded organizational
observations and scoped acceptance, including quarantine on late adverse information.
The [effect extension](work-effects.md) separately commits provider intent, administrative
authorization, conservative dispatch reservation and governed reconciliation. It does not send
network requests inside consensus or implement compensation automatically.
The [local control store](operational-storage.md) now persists control state and emitted archive
batches atomically. [Settled-work retirement](work-archive.md) reclaims bounded active slots;
credential retirement and the complete dependency/effect graph remain pending.

## Authenticated transitions

`checkedflow.operational_runtime.Runtime` accepts raw signed v2 bytes and authenticates against
its own immutable state registry. The pure transition is in `checkedflow.core.operational`.
Genesis fixes one mission, four organizations, one initial administrative key per organization
and at most 64 actor slots. Keys cannot be shared across roles/identities. State starts paused.
Its hash binds the explicit `checkedflow/control-state/v2` profile; unsupported profiles reject
at startup. This does not change the legacy `checkedflow/v1` runtime or its state hash.
Organization names, credential identities and actor slots are sorted by Unicode scalar-value
order at genesis. State hashing uses RFC 8785 over the corresponding JSON record, with tuples
represented as arrays. String normalization is not performed.

| Command | Effect | Required approval |
|---|---|---|
| `mission.pause` | Record paused mode | Three administrative organizations |
| `mission.drain` | Record draining mode | Three administrative organizations |
| `mission.resume` | Record running mode | Three administrative organizations |
| `history.archive` | Archive selected settled records from retired epochs while paused | Three administrative organizations |
| `journal.rollover` | Retire active request IDs, carry nonces, emit archive batch | Three administrative organizations |

Mission mode commands and `journal.rollover` have payload exactly
`{"mission":"CONFIGURED_MISSION"}`. `history.archive` uses the explicit selection and predecessor
fields in the [retirement contract](work-archive.md).
The additional [key lifecycle commands](key-lifecycle.md), `key.schedule` and `key.revoke`, have
their own exact payloads and the same three-organization approval rule. Unknown kinds and extra fields
reject. The worker SDK checks these modes and its local watchdog before new execution. Full
service drain, orphan cleanup and external-effect recovery still require qualification. No wall clock, storage, network
or candidate process runs in a transition. An explicit height tick survives rejected commands.

Repeated admitted commands acknowledge without repeating their state effect. Replaying an old
pause after a newer resume therefore does not pause the mission again. Conflicting bytes under
the same request ID reject. New commands advance actor nonces once; rejected commands do not.

## Bounded receipts and reserved capacity

The [pure request journal](../src/checkedflow/core/request_journal.py) stores bounded current-epoch
receipts and fixed actor nonce slots. Its admission-class Boolean is internal derived accounting:
it is never accepted as a client's assertion of administrative authority. The runtime derives it
after checking command authority. Budget reservations, task/candidate admission and worker/verifier
commands use ordinary capacity; governance controls use administrative capacity. Clients cannot
select the classification in a payload. Administrative commands require three organization signatures.

Default limits are 128 ordinary receipts / 24,576 encoded bytes and 16 administrative receipts /
4,096 encoded bytes. Byte ceilings and counts are enforced separately; ordinary work cannot borrow
administrative capacity. Each maximum-size encoded receipt is 209 bytes. Nonce slots have their
own fixed 64-actor bound. These are receipt-accounting limits, not a bound for the eventual entire
task/capability/residual state. Full-state invalidation reserves still need implementation.

One additional checkpoint receipt is permanently reserved outside both active classes. A governed
rollover therefore remains possible when both classes are full. The checkpoint's own nonce is
recorded in the emitted archive and successor nonce table. No unbounded request dictionary or
whole-history copy is retained in active state. Completed archive batches are returned to the
caller rather than accumulated in this in-memory runtime.

## Request identities and archive commitment

Request IDs use `<epoch>:<nonempty suffix>`, at most 80 UTF-8 bytes, with an unsigned canonical
decimal epoch. Leading zeros, non-ASCII epoch digits and future epochs reject. A retired prefix
returns `RETIRED_REQUEST`, even if someone re-signs that old ID with the current epoch and nonce.
Successful duplicate acknowledgments are retained only within the active epoch. Historical
lookup and replay are separate from new-command admission.

The archive commitment is SHA-256 over this byte sequence, in order:

1. UTF-8 `CheckedFlow/request-archive/v2`, then a zero byte.
2. Previous archive root as 32 raw bytes (all zeros at genesis).
3. Epoch as an eight-byte unsigned big-endian integer; receipt count as four bytes.
4. Each ordered receipt: four-byte length, then the receipt encoding.

A receipt encodes request ID and actor as separate four-byte byte-length prefixes plus exact
UTF-8 bytes, nonce as eight bytes, command SHA-256 as 32 bytes, and administrative class as one
byte (0 or 1). Integers remain within the portable protocol range. Order is committed admission
order, including the checkpoint receipt last. This binary framing is explicitly separate from
RFC 8785 command/state hashing. The root commits history; it is not a proof that bytes remain
retrievable or a source of snapshot bootstrap trust.

## Qualification boundaries

The tests exercise duplicate acknowledgment, conflicts, byte/count saturation, checkpoint reserve,
nonce continuity, retired-ID rejection, archive ordering, a generated-operation reference model
and a fixed CPU-only 5,000-request / 64-active-receipt run with two actors. This crosses the old
request-count limit for this component only. It does not qualify artifact longevity, budget
conservation, multi-host recovery or the full G5 operating envelope.

Before network deployment, the consensus adapter must bind ordering to the local store's atomic commits,
verify archive availability and preserve all active work, budgets, fences, keys, dependencies and
residuals across rollover. None may be dropped to make a checkpoint fit. These full-runtime
obligations remain open in the [implementation ledger](implementation-0.2.0.json).


## Governed funding extension

The state also contains the [work-budget ledger](work-budget.md). `budget.configure`,
`budget.reserve` and `budget.settle` require the same current administrative quorum.
Reservation receipts use ordinary capacity; funding configuration and settlement use the
administrative reserve. The ledger does not introduce task execution authority. Its required
state member extends an unpublished v2 development format and leaves v1 replay unchanged.
