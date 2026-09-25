# Audit against the original plan — 2026-09-25

## Scope and conclusion

This audit compares the local CheckedFlow 0.1.0 source, contracts, tests, documentation and
distribution workflow with the accepted implementation plan. It inspects code paths and reruns
checks; it does not infer completion from the previous delivery report. The current release audit
also covers the subsequently authorized public repository and package publication procedure.

The bounded reference runtime implements the planned generation, verification and reuse loop.
The review found recovery and transport defects and missing newcomer documentation. The fixes
below preserve the v1 signed-command format. Production independence, general novelty and
causal growth remain outside the demonstrated guarantees. Fresh execution results and exact
environment details are recorded separately in [validation-status.md](validation-status.md).

## Findings and corrections

| Finding | Consequence | Correction and regression evidence |
|---|---|---|
| Pure replay represented commands only. | An ending empty block could expire state that replay failed to reproduce. | Explicit `BlockHeight` events, an expiry conformance vector and `test_pure_replay_includes_empty_block_expiry` in [core tests](../tests/test_core.py). |
| Generation finish and proposal were separate, with source retained only in worker memory. | A crash or lost finish response left completed generation unregistered. | Persist exact source in the signed receipt; restart proposes from that receipt without execution. [Worker recovery tests](../tests/test_worker_recovery.py) inject the lost response and check budget/proposal uniqueness. |
| Missing adapter fields could escape as lookup/type errors. | A known adapter failure was not promptly recorded as an uncertain attempt. | Convert malformed specifications to charged unknown receipts and retained residuals; [worker recovery tests](../tests/test_worker_recovery.py). |
| Docker daemon probing could raise an uncategorized timeout. | A missing prerequisite was not consistently reported through the failure contract. | Return `SANDBOX_UNAVAILABLE` without dispatch; [boundary tests](../tests/test_boundaries.py). |
| Eligibility recursively revisited shared ancestors. | A dense bounded dependency DAG could cause exponential checking work. | Iterative traversal visits each ancestor once while preserving declared DFS order; [core traversal regression](../tests/test_core.py). |
| Source-string limits also constrained base64 RPC transactions; CometBFT's default body limit was smaller than a valid encoded envelope. | Large valid commands could fail before consensus admission. | Separate transport limits and configure a 2 MiB RPC body ceiling. [Transport tests](../tests/test_transport.py) and a real large-command case in [four-node tests](../tests/test_integration.py). |
| ABCI bounded canonical envelopes but not original transaction bytes. | A padded proposal could pass semantic checks yet exceed the journal's raw-byte bound. | Enforce 1 MiB before parsing in every ABCI transaction path; [ABCI boundary tests](../tests/test_abci.py) verify exact-limit admission and over-limit rejection. |
| CLI recovery loaded one bounded archive and capped each transaction list at 1,024. | Long histories or a valid block with many rejected transactions could not replay. | Streaming JSONL, exact block schema and outcome validation, `Store.iter_blocks()`; [transport/recovery tests](../tests/test_transport.py). |
| Contract discovery and static checks omitted some resources and anchors. | A new agent could miss the state/generator contracts or follow stale heading links. | CLI exposes every schema; packaging requires them; static inspection checks Markdown anchors and pinned proto source hashes. |
| The fork-exhaustion test accepted only timeout. | Actual early resource termination was misclassified as a failure of containment. | Require a successful same-profile preflight and accept bounded timeout or observed resource-failure exits, while rejecting launch/cleanup failures. |
| README's SDK fragment used undefined application variables. | A first-time reader could not run it directly or distinguish synthesis from verified execution. | Runnable packaged SDK command, vocabulary, staged tutorial, accounting example and documentation index. |

## Requirement coverage

“Implemented” means a concrete local code path exists. “Qualified” is limited to the observations
in the current validation record. “Prepared” describes configuration that has not been executed
against GitHub or PyPI. A limitation is not converted into a passing test.

| Original requirement | Implementation and evidence | Boundary or status |
|---|---|---|
| Python 3.12+, uv, src layout, Apache-2.0, 0.1.0 | [Package configuration](../pyproject.toml), [license](../LICENSE), installation checks | Implemented; matrix execution is recorded separately. |
| 18 software and 19 paper sources with versions, hashes and limits | [Research map](research.md), [machine registry](../src/checkedflow/data/research.json) | All 37 mapped; selected principle review is not theorem verification. |
| Pure transitions, replay and effect separation | [Core](../src/checkedflow/core/machine.py), [architecture](architecture.md) | Includes empty-block advancement; pure contexts require prior authentication. |
| Claims, evidence, authority and agreement separated | [Identity](../src/checkedflow/identity.py), [ABCI](../src/checkedflow/distributed/application.py), [concepts](concepts.md) | Consensus cannot establish external truth. |
| Residual preservation and evidence withdrawal | [Residual tests](../tests/test_residuals.py), [state rules](state-machine.md) | Resolution retains history; withdrawing resolution evidence reopens obligations. |
| One reserved/charged budget for every phase | [Budget model](../tests/test_core.py), [accounting example](tutorial.md#4-interpret-a-budget-without-hiding-verification) | Declared upper bounds; not actual CPU/financial metering. |
| Scoped reuse and dependency invalidation | Immutable IDs, scope checks and transitive expiry/revocation in core | Mission/contract labels require an appropriate real-world interpretation. |
| Copies, reuse, external input and loss separated | [Accounting definitions](state-machine.md#bounds-and-accounting) | Mission-local live behavior counts; external origin is declared; no general novelty or growth theorem. |
| Finite code formation and matched scratch comparison | [Synthesis](../src/checkedflow/synthesis.py), [laboratory](../src/checkedflow/distributed/demo.py) | Closed grammar, 31 inputs; round/depth bounds refer to dependency lineage. |
| Portable canonical JSON, schemas and conformance vectors | [Protocol](protocol.md), [contracts](../src/checkedflow/data/envelope.schema.json), [vectors](../src/checkedflow/data/vectors.json) | Schema validity alone does not establish semantic validity. |
| CometBFT 0.40.0, four equal fixed organizations, independent stores | [ABCI](../src/checkedflow/distributed/application.py), [storage](../src/checkedflow/storage.py), [runtime lock](../deploy/runtime-lock.json) | Real four-node laboratory; actual organizational independence is a deployment prerequisite. |
| Signed scope, committed leases/fences, idempotency and uncertain outcomes | Core/contract tests and [worker](../src/checkedflow/worker.py) | Bundled worker refuses external effects and does not automatically rerun started attempts. |
| Three approvals for governance and independent verification | Administrative quorum and organization-scoped votes | Three passes necessary; negative evidence conservatively vetoes reuse. |
| gVisor, fixed image/argv, resource bounds, no network, fail closed | [Runner](../src/checkedflow/runner.py), [isolation tests](../tests/test_integration.py) | Trusted operator host/daemon; no remote runtime attestation. |
| Static, contract, state-model and selected mutation checks | [Check entry point](../scripts/check.py), [verification scope](verification.md) | Statement and branch thresholds each 95%; selected mutations are not an exhaustive proof. |
| Real partitions, crashes, conflict, invalid proposals and quorum loss | [Four-node tests](../tests/test_integration.py), [ABCI commit tests](../tests/test_abci.py) | Actual TCP isolation; not exhaustive Byzantine schedule exploration. |
| English docs and agent entry points | [Documentation index](README.md), [skills](../skills.md), [SKILL](../.agents/skills/checkedflow/SKILL.md) | Runnable beginner path plus machine-readable contracts. |
| Installed SDK/CLI, extras and resources in wheel/sdist | [Package checker](../scripts/package_check.py) | Installed outside checkout; external CometBFT/gVisor remain separate. |
| Exact Trusted Publisher, manual release, OIDC only publish job, no rebuild | [Workflow](../.github/workflows/workflow.yml), [release gate](../scripts/release_gate.py) | Immutable action pins, disposable Linux qualification and exact tested artifacts; actual publication is recorded in GitHub releases and PyPI. |

## Agent interoperability extension review

The additional A2A/MCP request is implemented as optional boundary adapters. The original core
and signed v1 state contract are unchanged. The review identified integration hazards and
made them explicit in code, tests and the [interoperability guide](interoperability.md):

| Hazard | Design and evidence |
|---|---|
| Protocol SDKs normalize JSON numbers or duplicate keys before signature admission. | Transport inner signed JSON as a string; strict parsing precedes schema/signature checks. Negative lexical tests cover both format ambiguity and byte bounds. |
| A protocol credential or successful response is mistaken for authority, finality or verification. | Separate bearer/process visibility from signatures; preflight then confirmed command digest; no keys or code execution in the gateway. Task metadata distinguishes work completion from capability acceptance. |
| A new per-protocol task store diverges from consensus or retries after a lost reply. | Both protocols read the same node-backed mission gateway, with no independent ledger and no automatic submission retry. `OUTCOME_UNKNOWN` preserves ambiguity. |
| An incomplete successful HTTP response was classified as a definite transaction rejection. | The own-node client now reports missing admission/execution outcomes as `OUTCOME_UNKNOWN`, preserving possible commit ambiguity. Regression tests distinguish incomplete replies from explicit nonzero rejection codes. |
| Resubmitting a committed envelope reached CometBFT's duplicate transaction cache. | After authentication, scope and conflict checks, the gateway acknowledges an already committed digest directly. The four-node cross-protocol case exposed this behavior beyond the in-memory unit backend. |
| Missing standard protocol operations. | Durable observation journal, A2A listing/history/streams/subscriptions/push/extended card, three transport bindings, and MCP HTTP/templates/prompts/completion/notifications are implemented and tested. The [conformance matrix](conformance.md) defines the exact server role. |
| Callback egress can cross the operator's network boundary. | HTTPS host allowlist, public-only DNS and pinned addresses, original TLS identity, no redirect, bounded retries and credential-safe errors. In-flight configuration replacement cannot acknowledge a new destination. |
| HTTP access requires a transport-level identity without granting signing authority. | All agent endpoints are protected; MCP additionally supports external OAuth issuers with audience/scope checks and operator-installed public JWKS. |

The public API is `Gateway(Backend, chain, mission)`, `create_app(...)` for A2A and
`create_server(...)` for MCP. Protocol SDKs are optional extras and prohibited dependencies of
the common boundary/core layers. Schemas, machine profile, client example, CLI entries, operating
skill and package checks cover the new surface. The release gate requires an additional real
four-node A2A/MCP exchange. Consult the current validation record for executed results rather
than treating this implementation map as proof that a test ran.

## Remaining operational limits

The finite state admits 64 artifacts, 4,096 task records and 4,096 request identities, with a
4 MiB admission bound. This is a bounded experiment runtime; indefinite operation, pruning,
dynamic organization changes and in-place migration are not implemented. Bound exhaustion may
stop new work, including governance commands. Operators must plan successor deployments before
exhaustion, rather than assuming these caps provide denial-of-service resistance.

The original plan's one-organization fault model applies to consensus under CometBFT's assumptions.
Artifact acceptance deliberately prioritizes retaining negative evidence over availability.
Counterfactual search comparison remains one finite sequence, including outcomes where reuse
is worse. It cannot establish a general acceleration claim.

Before an authorized public release, follow [releasing.md](releasing.md): review immutable action
pins, provision a disposable qualification runner and protected publisher environment, then rerun all
gates against the exact artifacts. Historical local reports cannot substitute for that workflow.
