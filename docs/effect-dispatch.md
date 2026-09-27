# Supervised dispatch of approved drafts

Status: development SDK component. It connects an existing v2 reservation to the draft provider;
optional [Git staging](git-staging.md) requires a v2 policy. Compensation and G3 qualification remain unfinished.

## Problem and boundary

Yesterday's passing verification does not prove today's evidence is available. An approved
destination may be disabled while the provider reads its branches. A node may stop making
progress while the dispatcher's local process remains healthy. `checkedflow.effect_dispatch`
checks these conditions before provider access and again at its final send boundary.

`Dispatcher` receives a `Drafts` provider, an own-node `Watchdog`, a protected `Policy`, a scoped
artifact store and trusted `Access`, plus one executor identity/revision. It accepts only an
already committed reservation; it neither prepares nor authorizes one. Candidate code never
receives these objects, the token, policy path or provider journal.

## Operator policy

Discover the closed contract with `checkedflow schema effect-policy`. The UTF-8 file is limited
to 16 KiB, rejects duplicate JSON keys and unknown fields, and contains these fields:

| Field | Meaning |
|---|---|
| `profile` | Exactly `checkedflow/effect-policy/v1` |
| `chain`, `mission` | Own-node execution scope |
| `repository`, `repository_id` | Destination name and immutable numeric identity |
| `actor` | Expected GitHub account that creates the draft |
| `executor`, `revision` | Current mission signing identity and revision |
| `enabled` | Must be the boolean `true` to dispatch |
| `intents` | Sorted, unique list of up to 64 exact `Intent.digest` values; empty denies all |

Store the file and its parent outside candidate and gateway write access. Provision filesystem
permissions separately; parsing the file does not establish its ownership. Review each complete
intent and destination before adding its digest. The canonical policy digest must match the
quorum-approved effect record. Whitespace changes preserve the digest; adding an allowed intent,
changing identity or any other semantic edit requires corresponding renewed approval. Never use
a new intent or reset a journal to bypass an unresolved operation.

The fixed provider profile still requires destination Actions to be disabled and the repository
patch contract excludes protected paths. The policy does not offer a switch to permit privileged
candidate CI, merging, production deployment or arbitrary remote endpoints.

## Dispatch ordering

The operator establishes a watchdog using its validating full node, finite read/stall deadlines
and normal polling. A cold watchdog requires progressing committed observations. Construct the
dispatcher only in the isolated executor service with its exclusive durable provider journal.

1. Refuse a locally disabled provider. Poll the own node and require a current running observation.
2. Reload operator policy and resolve the exact live reservation into provider arguments.
3. Reconstruct the accepted patch using `repository_reuse.prepare`. Read and verify the exact
   source, patch, inventory and all retained verification reports from the scoped artifact store.
4. Poll the own node again. Recheck reservation/authority and reject a changed observation
   inventory. Reload policy and recheck watchdog freshness after these potentially slow reads.
5. Run the provider's complete-source Git binding and destination preflight. After its durable
   unknown claim, repeat steps 1–4 through `before_send`, requiring unchanged arguments and bytes.
6. Return the provider observation. A `confirmed` observation is not a signed consensus report.

The SDK call is `dispatcher.dispatch(effect_id, intent, contract, inputs)`, where `inputs` is the
existing `repository_reuse.Inputs` reference bundle. No generated code executes in these checks.
Adapters must impose bounded I/O timeouts. Local policy reloads and watchdog checks cannot make
separate systems atomic or cancel a request already sent. Artifact replication and availability
after the final check remain separate operational concerns.

## Recovery and remaining work

A failed entry check raises before provider access. A failed final check returns the provider's
`unknown` observation and keeps its durable claim. Unexpected programming exceptions may propagate;
they still do not remove the claim. Consensus already charges the reserved ceiling conservatively.
Never recreate its journal, resend under another ID, or interpret missing remote objects as retry
permission. Once the reservation expires or observations are recorded, this dispatch method is
no longer eligible; it is not a recovery method for historical effects.

The owning service must retain original signed submissions, publish bounded observation evidence
and report through the [nonce coordinator](worker-supervision.md). The
[durable supervisor](effect-supervision.md) now performs ordinary reservation, one invocation,
evidence publication and signed reporting with conservative local crash recovery. It must use governed read
reconciliation when the result or reporting window is uncertain. Least-privilege production
token custody, compensation, retirement
and separate-host failure qualification remain unfinished. Source fixtures use signed state and
actual SQLite artifacts with fixed provider responses; they do not claim a live GitHub gate.

See [effect states](work-effects.md), [provider guarantees](github-drafts.md) and
[dispatch freshness](dispatch-watchdog.md) for the underlying contracts.
