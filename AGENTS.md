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
Keep the publication interlock closed while any mandatory gate remains unqualified.

Draft effect integration must use complete-source/patch Git tree binding, not just a caller's tree
ID. See docs/github-drafts.md. Keep SHA-256 application identities distinct from Git SHA-1 IDs;
never execute candidate code or checkout filters to calculate these bindings.

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
