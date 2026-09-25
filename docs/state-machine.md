# State transitions and invariants

The [command catalogue](../src/checkedflow/data/commands.json) and
[envelope schema](../src/checkedflow/data/envelope.schema.json) define exact fields.

| Command | Authority | Effect |
|---|---|---|
| `worker.register`, `worker.revoke` | Three organizations | Add a distinct worker key or revoke it; revoke supporting memory on key loss. |
| `verifier.register` | Three organizations | Register an immutable checker contract, image and entrypoint. |
| `mission.create` | Three organizations | Fix workers, budget, protected verification capacity, image, receiver, lifetime and formation bounds. |
| `task.create` | Mission producer | Create generation, execution or repair work with explicit dependencies and maximum cost. |
| `task.lease` | Mission executor or scoped verifier | Reserve cost, increment fence, establish owner and deadline. |
| `task.start`, `task.heartbeat` | Live lease owner | Confirm execution start or renew the block-height lease. |
| `task.finish` | Started live lease owner | Charge the reservation and retain evidence; verifier votes bind artifact and checker scope. |
| `task.reconcile` | Three organizations | Explicitly retry or abandon an uncertain attempt; preserve history. |
| `capability.propose` | Producer owning completed generation | Store source and provenance, reserve four checks, create scoped verification tasks. |
| `capability.revoke` | Three organizations | Revoke and propagate quarantine to descendants. |
| `residual.resolve` | Three organizations | Attach a checked repair explicitly naming the residual; retain the original reason. |

Task states are `ready → leased → running → finished`. Timeout or an unknown receipt produces
`uncertain`. An administrative reconciliation moves it to `ready` or `abandoned`. Retrying
increments the fence and consumes a fresh maximum-cost reservation. A late result cannot finish
an expired fence, even if the original external action later succeeds.

Capability states are `candidate → checked`, or `quarantined`, `revoked`, `expired`.
Three passing votes with a matching behavioral digest admit an exhaustive contract. A negative
vote or contradictory behavioral digests quarantines the artifact while preserving votes.
Unused verification reservations are refunded; already running checks retain their reservation
until completion or timeout. An unsupported novelty judgment is represented without a digest.

## Invariants

A2A/MCP transport acknowledgments introduce no additional transition or approval state.
`status=committed` confirms a command digest in the own-node state; A2A `COMPLETED` projects a
finished work receipt. Neither changes capability acceptance. Inspect the capability's current
status, scope and ancestry before reuse. The [agent profile](interoperability.md) maps protocol
states to these existing records and retains uncertain outcomes as `INPUT_REQUIRED`.

1. `0 <= spent`, `0 <= reserved`, and `spent + reserved <= budget` for every mission.
2. Every started attempt has a committed lease, matching owner and current fence.
3. Every checked reusable dependency remains checked, unexpired and compatible through its ancestors.
4. Generated code cannot modify worker or verifier authority. Three organizations must authorize governance.
5. Request IDs are idempotent for identical command bytes. Reusing an ID with changed bytes is rejected.
6. Nonces increase by one per actor. A duplicate accepted request does not recharge the budget.
7. Rejected commands do not mutate input state. Block-height advancement still occurs for the block itself.
8. Failure and uncertainty records are preserved. Resolution adds checked evidence instead of erasing history.

## Bounds and accounting

The shared v1 state admits at most 64 artifacts. Missions may specify a lower ceiling (up to 256 in the wire field), 16 formation rounds, 16 dependency depth and 16 attempts
per task. Each task has at most 16 dependencies; the state admits at most 4,096 tasks and
4,096 command identities. Source is bounded to 262,144 UTF-8 bytes. State admission is capped
at 4 MiB of compact UTF-8 JSON, leaving serialization space for expiry-generated residuals.
Organization identities are at most 80 characters, capability IDs 80, and producer task IDs 160 so derived verification and residual identities remain addressable. These are finite v1 limits, not tunable distributed configuration.

Each task charges its declared upper bound on completion or timeout. Unknown metering never
becomes a zero charge. Verification is funded at proposal time, including the fourth organization's
check. The demonstration records actual grammar candidates separately from reserved work units.
`unique_generated_behaviors` is a live set cardinality, not a claim about causal originality;
`withdrawn_artifacts` counts withdrawn records, not a unit-compatible capability-loss theorem.

`max_rounds` and `max_depth` bound the longest dependency lineage. They do not limit the total
number of independent root tasks. `max_candidates` limits proposed artifacts per mission; the
reference worker also uses that value as its per-search candidate ceiling. Global artifact and
command bounds can be reached earlier. These limits are fixed before work begins and are not
automatically raised when a search or deployment reaches them.

| Accounting field | Exact scope |
|---|---|
| `unique_generated_behaviors` | Distinct nonempty behavior digests of live `generated` artifacts in this mission. |
| `copies` | Live generated artifacts with known behavior minus that distinct count. |
| `external_artifacts` | Live artifacts explicitly declared `external`; origin is not inferred from bytes. |
| `unknown_novelty` | Live artifacts whose contract has no exhaustive behavior identity. |
| `reuse_calls` | Declared dependency references counted at committed task start, including attempts that later fail. |
| `withdrawn_artifacts` | Artifact records currently revoked, expired or quarantined. |

The counters distinguish categories without equating their units. They are not global historical
discovery counts: withdrawing an artifact reduces the live set, and an equivalent later artifact
may restore it. Cross-mission novelty and causal endogenous growth are not computed.

## Dependency and residual history

Dependencies can name only previously checked immutable capability IDs. A new capability cannot
refer to itself, refer forward or replace an earlier ID to introduce a back edge. Shared
ancestors are checked once per eligibility walk, keeping work proportional to the reachable
graph. Every ancestor must meet the same lifetime and scope conditions.

Residual IDs retain the task/attempt or invalidation cause. A reconciliation decision records
its reason without deleting the original uncertainty. `residual.resolve` requires a checked
capability whose generating task explicitly names the residual in `spec.repairs`; an unrelated
successful artifact cannot close it. Withdrawing that resolution capability reopens the residual.

## Committed time and replay

Expiry uses block height, including empty blocks. For authenticated SDK events, pass a
`BlockHeight(height)` to pure `replay` when no command carries that height. Replaying journals of
signed transactions belongs at the boundary in [protocol.md](protocol.md#replay-and-block-journals).
Advancing height is not a worker command and cannot be asserted by an untrusted payload.

## Agent protocol projections

The [A2A/MCP gateways](interoperability.md) translate standard requests into the same signed
commands. There is no privileged transport-only transition. In particular, A2A CancelTask maps
to `task.reconcile` with `retry=false`, subject to the uncertain-state and three-signature rules.
Disconnecting a stream leaves state unchanged. Journal timestamps and callback delivery attempts
are transport observations; they do not consume mission work budget or advance block height.
