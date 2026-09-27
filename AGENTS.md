# CheckedFlow contributor instructions

Keep the core deterministic and independent of transport, storage, cryptography and runners.
Read docs/protocol.md before changing wire semantics. Preserve unknowns and unresolved obligations.
Do not confuse quorum agreement, checker acceptance, execution authority or external truth.
Run `uv run python scripts/check.py` before delivery. Integration qualification must run against
real CometBFT and gVisor; skipped tests cannot authorize publication.
Do not perform GitHub operations or publish to PyPI without explicit user authorization.

Operational v2 changes must update the requirement ledger and relevant human/machine guides.
Scheduling retries are not execution retries: preserve pending command bytes, execution records,
unknown outcomes, fixed call limits and boot-bound deadlines. Do not delete a journal or mint a
new task/plan identity to bypass recovery. Before the 0.2.0 release, audit README, every applicable
Docs page, this file and skills.md against actual installed-artifact behavior and G1-G7 evidence.
For replicated artifacts, count only fresh byte-verified reads from the protected configured set.
Do not cache an availability observation as authority, treat labels as independent failure domains,
or bypass retention to restore a missing copy. See docs/replicated-artifacts.md.
Keep the publication interlock closed while any mandatory gate remains unqualified.
Monitoring observations never authorize work. Keep /healthz, /readyz and /metrics inside the same
authenticated mission guard, preserve response-time revocation, and omit unavailable measurements
instead of substituting zero. Probe configuration belongs to the operator, never the candidate.
Reject an existing submission journal with a missing identity or foreign tables; never initialize
over partial recovery state. Keep original signed bytes and all related executor journals together.
Apply the same rule to worker attempts, schedules, sandbox ownership and agent callback journals.
Commit initial schemas and identity records atomically; missing tables are not fresh inventories.

Draft effect integration must use complete-source/patch Git tree binding, not just a caller's tree
ID. See docs/github-drafts.md. Keep SHA-256 application identities distinct from Git SHA-1 IDs;
never execute candidate code or checkout filters to calculate these bindings.
Operational provider callers must use the final `before_send` check after remote preflight.
Do not remove a durable claim when that check rejects or crashes; it still prohibits retry.
Use the supervised dispatcher to bind current protected policy and artifact evidence. Never treat
its provider observation as a committed report or allow candidates to modify the policy path.
Preserve the effect supervisor, coordinator and provider journals together. A recovered invocation
claim means unknown, never permission to call the dispatcher again. Verify published observation
bytes before reporting, and use governed reconciliation when the reporting window has closed.
Use effect_reconciliation.Reconciler for historical GET-only inspection. Its unsigned proposal
needs current administrative review and quorum signatures; absence never permits another send.
Optional staging requires explicit v2 policy approval and a deterministic planned head. Recheck
authority before each of its at most four writes. Never resume a partially claimed staging sequence
or update an existing reference. See docs/git-staging.md; unknown partial objects remain obligations.

V2 effect reservations retain full modeled cost once a send becomes possible. Never reset a
reservation, clear unknowns, release attached funding or mint a fresh intent to retry a provider
request. Use current quorum reconciliation and preserve compensation obligations. Read
docs/work-effects.md before connecting the provider, nonce coordinator or archive to effects.

Agent transports must use the selected version's gateway projection. Do not translate v1 signatures
or state objects into v2 authority. Test protocol discovery, original-byte submission, unknown replies
and current key/epoch checks through the same A2A/MCP implementations used by the CLI.

Callback credentials must stay sealed in persistent journals and absent from transport responses.
Never add plaintext fallback, auto-generated persistent deployment keys or silent legacy migration.
Preserve delivery state during explicit key rotation and test real process-exit windows.

Use the packaged access-roles contract for every client command decision. Never trust role claims in
messages or lose issuer/client/subject binding. Recheck policy at egress and callback dispatch; preserve
expired/revoked callback records for explicit reconciliation. Administrative transport grants still need
current quorum signatures. V2 CLI deployments require an explicit protected access-policy file.

Artifact downloads require a protected mission publication catalog as well as current client policy.
See [authorized downloads](docs/artifact-download.md) and `checkedflow schema artifact-publication`.
Never turn a client-supplied digest/reference or an unverified state pointer into publication authority.
Keep catalogs outside candidate workspaces. Archive downloads do not establish trusted replay roots.

For [agent TLS](docs/agent-tls.md), provision certificate/key/client-CA files independently of signing
and callback keys. Never infer roles from certificates or forwarded headers. Keep loopback defaults,
require all TLS settings together, and drain/restart all listeners to retire a CA snapshot. Do not
claim a proxy template or source handshake test proves installed multi-host deployment qualification.

Telemetry must not change original operation returns or exceptions. Keep exporters outside consensus and
protected work. Never turn local step observations into committed event counts or recoverable authority.
Do not log raw exceptions, candidate text, tokens or arbitrary trace parents. See docs/telemetry.md.

Monitoring rule changes require actual pinned promtool evaluation, including pending/firing/recovery
and healthy-boundary cases. Missing metrics are not zero. Preserve explicit limits on quorum,
credential and evidence diagnoses; examples and recovery targets are not achieved SLOs.

Legacy inventory requires an independently trusted checkpoint and retains the complete old state.
Do not treat summaries as migration authority or charge already charged unknown attempts twice.
Successor activation and signer custody remain separate requirements; see docs/legacy-migration.md.
Prepared successor accounting holds inheritance separately from ordinary tickets. Never release
it through ticket settlement, remove its history root, or manufacture past verification charges.
Pristine successor preparation is not proof of old-node shutdown or administrator approval.
Succession approval requires separate old and new three-organization quorums against independently
protected expected roots and validators. Never treat signatures as physical custody or expose
approval signing to candidates. See docs/succession-approval.md.

Persist succession approval claims before signing and move the journal with signer custody.
Never delete or roll back a conflicting claim; see docs/succession-approval.md. Local SQLite
exclusion does not prove cross-host signer ownership or authorize successor activation.

Inherited ABCI startup must verify succession evidence before creating state or listeners.
Never derive the trusted checkpoint solely from the submitted manifest; retain independent
operator provenance. See docs/succession-approval.md#startup-admission.

Preserve validator lock inodes, private keys and latest signing state during maintenance.
A remote host being unreachable does not prove it stopped signing. See docs/validator-custody.md.

Use budget.reconcile_inherited only with current administrative quorum, the original checkpoint
and retained evidence. Unknown charges are not refundable; old tasks never become executable.
See docs/legacy-reconciliation.md.

Legacy retention verification must check an existing pin and fresh bytes; never silently recreate
a missing pin. History coverage needs independent replay authentication. See docs/legacy-retention.md.

Retained history replay requires independent genesis and final checkpoints and operator-authenticated
archive digests. Endpoint state equality alone does not authenticate state-neutral rejected records.
Use the legacy-history schema; see docs/legacy-retention.md.
