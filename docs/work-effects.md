# Governed external operations

Status: development v2 consensus and intent-binding implementation. The published 0.1.0 package
does not include it. This is not a complete unattended GitHub executor or a qualified G3 workflow.

## Why an accepted patch is not permission to publish

A patch may pass checks and still lack permission to change another system. An HTTP request may
also succeed while its reply is lost. CheckedFlow records these as separate facts: candidate
acceptance, approved provider intent, dispatch reservation, provider observation and administrative
reconciliation. Consensus commits the records; it never calls GitHub or executes candidate code.

Each effect pins a candidate, an unused execution-budget ticket, a complete intent digest, an
operator-policy digest and one mission-scoped `effect_executor` identity/revision. Preparation and
authorization are separate commands, each requiring three current administrative organizations.
The preparation command digest becomes the stable operation ID. The authorization command digest
binds the later provider plan. Different request IDs cannot reuse an already retained intent.

## Commands and states

The [command schema](../src/checkedflow/data/effect-command.schema.json) fixes the payloads.
All commands use the normal v2 signed envelope, chain, mission, epoch, nonce and revision rules.
The packaged [signed replay vector](../src/checkedflow/data/effect-flow-vector.json) includes initial
public identities, every command/height, per-step state hashes and the final compensation obligation.
Its keys are deterministic test fixtures, never deployment identities. The wheel/sdist smoke
replays it without a source checkout or provider access.

| Command | Required authority | Effect |
|---|---|---|
| `effect.prepare` | Three administrative organizations | Retain immutable intent, policy, candidate, funding, executor, expiry and dispatch window; candidate must currently be accepted |
| `effect.authorize` | Three administrative organizations | Authorize the unchanged prepared record while running |
| `effect.deny` | Three administrative organizations | Deny a prepared/authorized operation and release unused funding; never deny a possibly sent operation |
| `effect.reserve` | Exact committed executor revision | Enter `dispatch_reserved` while running; establish fence 1 and a finite height window; charge the full ticket ceiling conservatively |
| `effect.report` | Original executor revision and fence | Record `observed` with provider object number/evidence, or `unknown`; only during the live original reservation |
| `effect.reconcile` | Three administrative organizations | Retain an uncertain result or record the original observed object as `reconciled`; this never authorizes another send |

Unknown reports use number zero without erasing an earlier object number. Observations require a
positive number and evidence digest. Once recorded, a different object number is a conflict.
The only supported outcomes are `observed` and `unknown`; absence is not proof that sending again
is safe. Evidence digests identify signed assertions, not independent proof of provider truth.
The executor and reviewing administrators must inspect the exact remote object under the provider
contract. Previous assertions remain in the signed block history, even when a later observation
changes the current classification.

Before reservation, expiry, revoked authority or loss of candidate acceptance produces `expired`
and releases unused funds. After reservation, the same events or dispatch-window expiry produce
`unknown` and retain the full charge. Reconciliation never refunds a possible send. A subsequent
withdrawal/revocation of accepted evidence moves an observed/reconciled operation to
`compensation_required`. Compromise of the reporting executor similarly invalidates its unreviewed
observation; current administrative reconciliation can independently establish the remote object.

Pause/drain block new authorizations and reservations, while reports and reconciliation remain
available. Resume rejects `dispatch_reserved`, `observed`, `unknown` and `compensation_required`
records. A height deadline is not a wall-clock lease extension: the [dispatch watchdog](dispatch-watchdog.md)
must separately inhibit sending when own-node observations or quorum progress become stale.
Local emergency disable and current destination policy remain executor responsibilities.

## Bind consensus intent to GitHub bytes

`checkedflow.github_effects.Intent` has a closed [schema](../src/checkedflow/data/github-effect-intent.schema.json)
and `decode_intent` parser. It binds repository name/numeric ID, base branch/commit, staged head
commit, Git tree, resulting source-tree digest, verification-contract digest and exact patch digest.
`Intent.digest` is the canonical SHA-256 value funded and approved by the effect commands.

`reserved_plan(state, effect_id, intent, contract, executor=..., revision=..., policy=...)` checks
the validated current state, running mode, original reservation/window, executor, policy and exact
intent. It checks the candidate against the contract and **result tree**; the patch digest is
separately bound through the contract and intent. It also enforces the contract's draft permission
and deadline. It produces the exact `github_drafts.Plan`, including the committed operation and
authorization digests. Use [byte-bound dispatch](github-drafts.md) to compare complete source and
patch bytes with actual staged Git trees.

These calls do not authenticate an arbitrary remote state response, enforce a policy whose digest
the caller merely asserts, or make a snapshot remain fresh. Obtain state from the operator's
validated full node, verify current artifact availability and policy bytes, and recheck freshness
immediately before sending. The [nonce coordinator](worker-supervision.md) accepts the executor
commands and retains original bytes across uncertain submissions. The provider's separate private
journal must also survive crashes; consensus reservation alone is not remote exactly-once delivery.

The provider's `before_send(plan)` hook runs after its final remote lookup and durable local
claim. A trusted executor can use it to repeat the current reservation, policy and watchdog
checks before a new POST. Rejection preserves the unknown claim, so later callers cannot
silently retry without supervision. The hook is an integration boundary, not an implemented
full executor; see [its exact ordering and limitations](github-drafts.md#final-local-dispatch-check).
The [supervised dispatcher](effect-dispatch.md) now connects that hook to a reloaded protected
intent policy, current own-node reservations and complete stored verification evidence. The
[durable supervisor](effect-supervision.md) connects ordinary reservation, reporting and local
crash recovery. Staging, compensation and cross-host recovery remain separate requirements.
The [historical reconciler](effect-reconciliation.md) uses the original policy and source bindings
to publish GET-only evidence and an unsigned `effect.reconcile` proposal. Current quorum approval
remains mandatory; a paused dispatcher or revoked executor key cannot authorize another send.

## Persistence, bounds and remaining integration

Effects are part of the atomic v2 state and block replay. Empty effect collections are omitted from
encoding, preserving pre-effect v2 state hashes and all v1 behavior. Nonempty state is limited to
64 effects. Effects pin their candidates and funding, preventing work retirement from destroying
unresolved dependencies. Effect archival/compensation is not yet implemented; capacity exhaustion
fails closed. No record may be deleted or assigned a fresh intent merely to bypass this bound or
an uncertain result. Stop and reconcile rather than reset journals.

A2A/MCP mission inspection exposes native effect records. The command role catalog allows
executor reservation/reporting separately from preparation, authorization, denial and reconciliation.
Transport grants remain additional restrictions; they do not replace command signatures.

Source tests cover malformed snapshots, authority/funding misuse, duplicate intent, pause/resume,
expiry, adverse evidence, original object identity, generated observation sequences and SQLite
replay. The added four-node/gVisor qualification case checks actual candidate observations, node
death, automatic expiry, catch-up, administrative reconciliation and common hashes. Its provider
observation is explicitly a signed fixture, not a real GitHub result. Installed qualification must
execute this named case; a previous report lacking it cannot qualify this increment.

Still required for the complete effect lifecycle: governed staging and live reconciliation qualification;
production credential custody and cross-host executor recovery; exact-identity compensation
and retirement; complete crash-window
and multi-host qualification. Do not enable publication from these component checks alone.
