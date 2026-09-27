# Codex implementation prompt: CheckedFlow 0.2.0
## Operational hardening to a narrowly defined stable-service standard

You are implementing changes in **`kadubon/checkedflow`**:

https://github.com/kadubon/checkedflow

## Mission

Implement the practical-domain, deployment, security, storage, recovery, observability, and compatibility work described below. Deliver functioning code, tests, documentation, installable distributions, and an evidence-based release decision.

The owner wants the previously discussed **“1.0 Stable” operational scope**, but the **only authorized final package version is `0.2.0`, with Git tag `v0.2.0`**. Do not publish 0.3, 0.4, 1.0, or silently select another version. Milestones are implementation checkpoints, not separate public releases.

“Stable” is an acceptance target for a **specific documented deployment and workload profile**, not a label to apply merely because the implementation is large or the tests are green. Do not claim universal production readiness, formal verification of the whole system, independent organizational operation, or proven AI acceleration.

This is an implementation task, not a documentation-only exercise. Do not replace required implementations with interfaces that raise `NotImplementedError`, prose, fake receipts, mock-only backends, or an attractive deployment diagram.

**Preserve the central separations:** authorization, committed ordering, attempted execution, observed results, acceptance under a contract, current reusability, and resource accounting.

---

## 0. Authorization, cost boundaries, and truthful completion

### Authorized work

Within this repository, implement code, contracts, tests, migrations, deployment assets, documentation, and CI changes needed for this task. Use ordinary branches and pull requests. Subject to existing permissions and required approvals, prepare and publish the final **0.2.0** release through the existing release mechanism only after its gates pass.

This instruction authorizes the normal repository/release actions for this task. It does **not** authorize bypassing branch protections, environment reviewers, GitHub/PyPI policies, or security checks.

Do not change `kadubon/github.io`, the Collective Intelligence Index, companion projects, user organization settings, billing, or unrelated repositories. A later Index refresh is a separate task.

### Computation and external effects

The owner does not want expensive empirical LLM experiments. Accordingly:

- No paid model calls, model-weight downloads, GPUs, training, or broad AI performance campaigns.
- Use deterministic fixture producers and bounded CPU-based engineering tests.
- Real sandbox, consensus, network, storage, and recovery tests are required where specified. Do not misclassify these as experiments that may be skipped.
- Do not provision paid cloud resources, enlarge a billing plan, open public ports, alter a persistent operator Docker daemon, or run unbounded stress loops.
- Use disposable directories, containers, test identities, and already authorized ephemeral runners.
- Record resource/time limits before expensive test stages; stop and report at those limits rather than retrying indefinitely.
- Additional multi-host tests require an explicitly supplied/authorized test inventory. Do not invent infrastructure or bill the owner for it.
- The GitHub draft-PR effect implemented below is disabled by default. Exercise its real write path only against an explicitly authorized disposable target. A configured credential alone is not permission to mutate an arbitrary repository.
- Do not execute generated or submitted repository code on the trusted host or in a privileged CI context.

### Missing infrastructure is a scoped blocker

If a prerequisite is unavailable, finish all independently feasible implementation work, provide the runnable qualification harness, and report the exact blocked gate. Do not stop the entire task at the first missing service. Equally, do not replace a required real test with a mock and call it passed.

Do not publish the final 0.2.0 operational release if a mandatory gate for its declared supported profile is missing, skipped, or failing. Keep the PR/release preparation ready, with status **BLOCKED**, rather than publishing an incomplete release and calling it stable.

No fabricated results, future-dated evidence, inherited PASS labels, or unsupported timing/throughput promises.

---

## 1. Establish and preserve the actual baseline

Read applicable `AGENTS.md` and contribution rules first. Inspect the current default branch, published tags/releases, PyPI metadata, workflows, and repository rules. Preserve unrelated working-tree changes.

A source inspection for this prompt observed:

- Default-branch commit: `a58869e2488bed9550b006d261601240357b2b99`.
- Published 0.1.0 release source: `cbe6ad8670a8936683d911d5fe216a94fc3a1fff`.
- Package version: 0.1.0; Python 3.12–3.14 documented.
- Signed core protocol: `checkedflow/v1`.
- Four fixed organizations, equal validator power, and three-organization governance/acceptance rules.
- A finite integer-array domain, with 31 declared inputs.
- Whole-state hashing, a 4 MiB application-state admission cap, 64 capabilities, and 4,096 tasks/request identities.
- Block-height deadlines, not wall-clock deadlines.
- No supported snapshots/state sync or complete governance-key rotation.
- One-attempt workers without active sandbox heartbeats in the bundled supervisor path.
- Real A2A/MCP adapters and existing real CometBFT/gVisor qualification.

These are orientation facts, not permission to assume HEAD has remained unchanged. Verify them. If 0.2.0 already exists, do not overwrite it or choose another release number; report the conflict and separate already-completed work from missing work.

Inspect at least:

- `README.md`, `AGENTS.md`, `SECURITY.md`, `pyproject.toml`, `uv.lock`
- `docs/protocol.md`, `docs/state-machine.md`, `docs/architecture.md`
- `docs/adapters.md`, `docs/operations.md`, `docs/interoperability.md`
- `docs/security.md`, `docs/conformance.md`, `docs/verification.md`
- `docs/audit.md`, `docs/validation-status.md`, `docs/releasing.md`
- `src/checkedflow/core/`, runtime, wire, identity, storage, runner, worker, recovery
- `src/checkedflow/agents/` and `src/checkedflow/distributed/`
- Packaged schemas/vectors, `deploy/`, tests, `scripts/check.py`, package and release gates
- `.github/workflows/workflow.yml`

Run the existing inexpensive baseline checks first in a disposable environment. Preserve failures as baseline evidence and distinguish source checks from installed-artifact checks.

Create:

- `docs/implementation-0.2.0.md`: decisions, milestones, acceptance cases, dependencies.
- A machine-readable implementation status file, using the repository's conventions.
- `docs/operational-profile-0.2.md`: proposed deployment/workload envelope, explicitly marked unqualified until measured.

Track each requirement as `NOT_STARTED`, `IMPLEMENTED`, `SOURCE_TESTED`, `INSTALLED_TESTED`, `INTEGRATION_TESTED`, `DEPLOYMENT_QUALIFIED`, or `BLOCKED`, with evidence references. A count of checked boxes is not a release gate.

---

## 2. Define the supported 0.2 operational product

### Supported target

A **fixed-membership, four-operator, evidence-preserving runtime for bounded Python repository-patch work**, with:

1. A real `repository-patch/v1` domain.
2. Content-addressed source/patch/evidence storage outside consensus payloads.
3. Sustained bounded operation with safe archival/checkpoint behavior, not eventual administrative lockout.
4. Authenticated snapshot/restore and node bootstrap support.
5. Explicit upgrades and 0.1-to-0.2 migration without rewriting historical meaning.
6. Role-separated keys, rotation/revocation, and a real managed-signer integration.
7. Secure mission/client access and narrowly scoped artifacts.
8. One constrained external effect: optionally open a GitHub **draft** PR for an approved checked patch.
9. Supervision, admission control, observability, and operator runbooks.
10. Actual bounded multi-host qualification for the operational claim.

The core remains model-independent. External agents can propose work through the existing signed-command boundary. A paid model is not required to exercise the implemented lifecycle.

### Profiles

Keep profiles explicit:

- **SDK/offline:** local inspection and replay; no distributed-finality claim.
- **Laboratory:** four colocated nodes; tests mechanics, not organizational independence.
- **Operational:** four separate pre-provisioned Linux node environments, protected keys, authenticated transport, approved artifact store, and the declared bounded workload.

Do not introduce a “single-node production” fallback and preserve a three-of-four assurance claim. Do not silently downgrade operational execution to ordinary Docker or a host subprocess when gVisor is unavailable.

### Not required for 0.2

Do not build an agent framework, GUI, plugin marketplace, arbitrary autonomous code synthesizer, payments/email/deployment actuator suite, private computation, ZK system, universal verifier, or elastic validator membership.

Keeping four organizations fixed is acceptable. Key replacement, node replacement, and a governed successor/upgrade procedure still must work.

Do not implement both Kubernetes/Helm and every other deployment platform. One maintained local deployment path plus one real multi-host path is enough.

---

## 3. Freeze legacy meaning; version new consensus semantics explicitly

Before adding fields or changing transitions, write an ADR explaining the version boundary.

### Legacy compatibility

Preserve the interpretation of existing `checkedflow/v1` signed bytes, canonicalization, state hashes, commands, error codes, expiry behavior, and replay vectors. Preserve published 0.1.0 tags/assets and historical validation records.

Make frozen golden fixtures from authentic old histories. The new package must verify/replay them with the original semantics. Do not edit expected hashes to make a changed interpretation pass.

### New semantics

Use a new explicitly versioned protocol/state profile, such as `checkedflow/v2`, for changes that reinterpret committed data, including:

- Artifact references replacing inline source.
- Role-separated organization keys and rotation semantics.
- Archival/epoch/snapshot commitments.
- New action-authorization/effect states.
- New configurable consensus-bound capacity rules.

Package version **0.2.0** is separate from wire/state versions. Do not bump the package to 1.0 to express a v2 protocol.

Reject unsupported combinations at startup and request admission. Do not accept v1 bytes under v2 meaning. Do not downgrade a signed request automatically.

Preserve strict JSON lexical admission, duplicate-key rejection, canonical bytes, integer bounds, and domain-separated signatures. Network SDKs must not normalize untrusted input before the signed inner envelope is checked.

### Deterministic core

The state transition must not call S3, GitHub, Vault, clocks, DNS, randomness, model APIs, metrics, or a sandbox. All outside observations enter through bounded, authenticated, explicitly interpreted records. Local service availability cannot make different validators compute different state transitions.

Local derived indexes and caches are rebuildable, never independent authorization sources. Rejected commands must preserve the established command-versus-block-expiry distinction.

---

## 4. Implement a useful repository-patch domain

Implement **`repository-patch/v1`** as a restricted Python project profile, not “arbitrary repository execution.”

### Contract and immutable inputs

An approved contract must bind:

- Repository identity and exact base commit plus content/tree digest.
- Bounded normalized file manifest, patch digest, result-tree digest.
- Allowed paths/extensions, maximum files/bytes, and prohibited file classes.
- Approved dependency lock or prebuilt environment image digest.
- Exact verifier implementation, test selection, lint configuration, and expected test inventory.
- Resource ceilings and deadlines.
- Named receiver/use scope.
- Whether any eventual external PR effect is permitted; default false.

Do not resolve mutable branches during verification. Changing the base, dependency lock, checker, selected tests, or relevant environment creates a new verification target.

Use fixture repositories included under an explicit open license, containing small real Python modules with seeded bugs and independently specified expectations. Include more than trivial integer-array transformations, but keep tests CPU-only and bounded.

The producer may be a deterministic local fixture producer or an external agent supplying a patch through an authorized interface. Do not add an inference service to satisfy this requirement.

### Patch ingestion

Treat patches, archives, repository metadata, build files, filenames, and test code as untrusted.

Reject or explicitly exclude:

- Absolute/traversal paths, mixed-separator escapes, collisions, escaping links, hardlinks, device files, oversized archives, and decompression bombs.
- Git hooks, `.git` metadata, submodules, LFS fetching, executable filters, uncontrolled credential helpers, and network-driven dependency installation.
- Changes to protected tests, checker configuration, dependency policy, CI workflows, signing metadata, or security controls unless a separate newly approved contract explicitly allows them.

Validate paths on the actual host/target filesystem; do not rely on a string prefix. Use private workspaces, controlled extraction, no-follow/descriptor-based operations where appropriate, and protection against validation-to-use races.

Repository acquisition is a trusted, constrained host operation or an uploaded immutable bundle. Do not grant network access or credentials to candidate code. A generic repository URL must not become an SSRF, local-file, or arbitrary-git-protocol primitive.

### Verification architecture

Apply the patch in disposable isolated workspaces. Run candidate code only through the approved gVisor profile, with no network, secrets, Docker socket, writable host paths, or privileged mode.

Bind evidence to the exact patched bytes, base, environment, and checker. Zero collected tests, unexpected skips, malformed reports, timeout, missing sandbox, or uncertain cleanup must not produce a pass.

**Critical:** making test files read-only does not make a test process trustworthy when it imports hostile candidate code. Candidate code can interfere with the interpreter, pytest plugins, exits, and report files without editing protected tests.

Provide a restricted cross-process/output-checking path where the trusted evaluator owns the expected inputs/results and interprets bounded candidate outputs outside the candidate's process. Preserve the distinction between:

- Reported repository test/lint results under a pinned environment.
- Independently checked properties for a declared bounded interface.
- General program correctness, which is not established.

Do not trust candidate-supplied JUnit, stdout containing “PASS,” or a success Boolean as the authoritative verdict. Document residual shared-checker risks. Do not claim secret hidden tests when the candidate can inspect the wrapper or inputs.

For non-exhaustive repository tests, use a scoped test-suite acceptance claim and unknown general behavior/novelty. Do not relabel a digest of test outputs as universal program equivalence.

### Reuse

Distinguish reuse of an exact checked patch against its original base from extraction of a generally reusable procedure. Implement context checks and fresh verification for a new base/environment. Do not automatically cherry-pick a patch onto a new repository and inherit old acceptance.

Demonstrate the whole path:

approved mission -> candidate patch -> isolated execution -> independent checks -> accepted scoped artifact -> allowed same-scope reuse -> changed-base rejection -> new qualification.

Include a valid patch, an incorrect patch, test-tampering attempts, stale evidence, changed dependencies, and timeout cases.

---

## 5. Separate artifact bytes from consensus state

Implement a content-addressed artifact interface with:

- A local filesystem backend for offline/disposable operation.
- A real S3-compatible backend, exercised against an approved disposable local service or authorized endpoint.

Use provider-neutral interfaces. Select and pin any external service only after checking its current license, supported distribution, and compatibility. Do not invent a dependency or assume every “S3-compatible” backend has identical consistency semantics.

### References and integrity

Consensus records contain small typed references: digest algorithm/value, byte length, content type, artifact kind, scope, and manifest/version identity. Keep credentials, expiring signed URLs, and arbitrary endpoint addresses outside canonical state.

Every read verifies exact bytes and length. S3 ETag is not a substitute for the declared content digest. Handle partial uploads, concurrent identical puts, conflicting content, corruption, interrupted fetches, and bounded streams.

Uploaded bytes become visible only after complete verification and atomic finalization. Use per-scope storage policy; a hash is not authorization. Prevent cross-scope existence leaks, unauthorized presigned access, and unsafe cache reuse. Define encrypted-storage digest semantics explicitly.

### Availability is separate from integrity

A correct digest does not establish that an object can be retrieved. Use explicit publication/availability receipts or other documented admission rules to establish the required replicas/availability before allowing operations that need the artifact.

Do not fetch artifacts during consensus execution. Runtime use fetches and checks bytes; if data is unavailable, it stops with an explicit unavailable/unknown state. A local outage must not silently mutate the global verdict.

Test removal, corruption, stale replicas, and service outage after initial acceptance. Do not return “usable” solely because an old hash exists in committed state.

### Retention and garbage collection

Implement mark/retain/sweep or an equivalent safe policy with a dry run, protected references, pending-operation roots, historical retention rules, grace periods, and authenticated execution.

Retain live dependencies, unresolved effect evidence, required snapshot manifests, and retained replay material. Do not delete an artifact between admission and use: pin/version the referenced object for the operation.

Deletion/tombstoning, revocation, and physical erasure are distinct. Document and test restore-related resurrection prevention.

---

## 6. Make long-running state safe rather than merely increasing limits

The legacy global caps can eventually block governance as well as ordinary work. Preserve v1 replay, but do not carry that dead-end behavior into the new operational profile.

Implement a versioned bounded-state/archive strategy. Decide it in an ADR before coding. A bounded active set plus authenticated archived commitments and epoch/checkpoint transitions is acceptable; a transactional indexed state representation is also acceptable if its deterministic commitment and replay are specified.

Whichever strategy is chosen must provide functioning code for:

- Bounded active tasks/capabilities and paginated observations.
- Durable immutable history/commitments and selective materialization.
- Nonce, request-ID, fence, key, dependency, budget, and residual continuity.
- Safe rollover/checkpoint/archive without resetting unfinished obligations.
- Authenticated lookup of historical evidence when required.
- Predictable resource limits for admission, dependency traversal, and invalidation.

Do not solve scaling with an environment-variable increase to 64/4,096/4 MiB, an unbounded in-memory dictionary, or whole-history copying per task.

If old idempotency records are retired, specify the retention window and an explicit permanent `RETIRED_REQUEST`/equivalent result. A retired request must never become a fresh dispatch. Do not promise infinite successful duplicate acknowledgment if the design only retains a finite window.

Dependencies crossing archive/epoch boundaries need authenticated identity and lifecycle semantics. Missing archive evidence cannot be interpreted as valid or absent according to convenience.

### Administrative reserve

Ordinary work must not consume the capacity needed for pause, drain, revoke, reconcile, snapshot, and safe rollover. Reserve bounded storage/request/transition capacity for those actions and for invalidation/residual expansion.

Test byte-level as well as object-count exhaustion. A paused or saturated system must still be able to enter its defined safe state. No quorum or signature bypass is permitted.

### Accounting

Keep legacy declared-ceiling charges separate from optional observed CPU/token/currency measurements. Missing observations remain missing; counters do not turn into financial metering by relabeling.

Preserve budget conservation across retries, snapshots, rollover, and restore. A successor epoch or run is not a fresh spending allowance unless separately authorized. Carry unresolved liabilities explicitly and prevent double charging.

Support a bounded deterministic longevity test that crosses the old limits—for example, more than 4,096 accepted identities and more than 64 total artifacts over the lifecycle—without claiming any particular production throughput. Small artifact sizes and batch generation should keep this affordable. Record the test dimensions before running it.

---

## 7. Implement snapshots, restoration, bootstrap, and upgrades

### Snapshot format

Create a versioned manifest binding:

- Chain/genesis and protocol/state versions.
- Committed height and application-state commitment.
- Relevant consensus/validator identities.
- Chunk identities, sizes, order, total limits, and required artifacts.
- Archive roots, nonces, fences, revoked keys, budget balances, and unresolved operations.
- Schema/checker/runtime compatibility requirements.

Use bounded parsing, streaming, chunk verification, staged import, and atomic activation. A corrupt or partial snapshot must not overwrite a working store.

A self-consistent hash or three signatures in a manifest is not, by itself, bootstrap trust. Verification must use an already trusted genesis/checkpoint/validator set and the supported consensus trust model. Do not accept trust keys from the snapshot being verified.

### CometBFT integration

For the pinned supported CometBFT version, implement the actual snapshot/state-sync ABCI surface where required: listing, offering, loading chunks, applying chunks, and post-import state/hash verification.

Use the documented light-client/trusted-checkpoint mechanism. Verify the exact relation between snapshot height, commit height, and AppHash for that version; add off-by-one negative tests. Do not import semantics from a different documentation version merely because its API names look similar.

If the application cannot safely support arbitrary peer bootstrap, implement and document the narrower authenticated/operator-provisioned checkpoint profile rather than allowing untrusted snapshot bootstrap. The declared operational recovery gate must still be real, not a copy-file demonstration.

### Backup and disaster recovery

Provide CLI/runbooks for consistent backup, validation, restore into a new directory, dry-run inspection, and caught-up verification. Account for SQLite WAL and the A2A journal's non-reconstructible callback configuration.

Protect secrets separately. A snapshot must not embed reusable production private keys or callback credentials in public artifacts.

Do not restore validator signing state into two active nodes or roll it backward to enable double signing. Node replacement must include stop/drain and signer-ownership verification. An application snapshot is not a consensus signer backup.

Test disk/store loss, snapshot corruption, a stopped node rejoining, incomplete backup, stale checkpoint, unknown task restoration, and revoked-evidence restoration.

### Migration and upgrade

Implement a reviewed 0.1.0 -> 0.2.0 path using authentic old state/history fixtures, including uncertain work and revoked dependencies.

A maintenance-window, governed successor deployment is acceptable for a protocol-breaking migration. It must bind old and new identities/checkpoints, preserve history and outstanding state, stop old dispatch, and prevent old-chain commands from acquiring new authority.

Legacy captures retain their original meaning; migrating structure cannot mint verification evidence. Unsupported/missing original artifacts remain unresolved.

Within a compatible protocol line, provide a rolling binary-upgrade procedure that retains quorum. For incompatible protocol changes, explicitly reject unsafe mixed versions and use the coordinated procedure. Document the point beyond which rollback is unsafe; use forward recovery instead of silently restoring an older writer.

---

## 8. Production-oriented identity and access control

### Separate key purposes

In the new protocol, separate:

- CometBFT validator signing keys and anti-double-sign state.
- Organization administrative authorization keys.
- Worker/executor keys.
- Verifier identities and attestation keys.
- Gateway transport credentials.
- External-effect credentials.

Do not let a bearer token substitute for signed command authority. Do not give gateways or candidate code administrative signing keys. Keep each organization’s actual ownership boundary explicit.

Preserve the existing separation between consensus progress and artifact acceptance. A negative or contradictory verifier observation must remain visible and trigger the declared quarantine policy; do not discard it merely because a passing quorum was reached. Remaining funded checks and their costs must not disappear after early acceptance. A false/malicious negative may reduce availability; document that trade-off rather than inventing a truth oracle or weakening the policy silently.

Preserve the four-organization membership model. Three signatures from different keys controlled by one organization do not count as three organizations.

### Rotation and revocation

Implement pending -> activated -> retired/revoked key lifecycles, explicit activation heights/versions, proof of possession where required, and administrative approval under current valid authority.

Test overlap, old-key requests, mixed key epochs, replay, rotation failure, lost signer, and compromised-key revocation. Distinguish routine rotation from compromised historical evidence; do not automatically invalidate all old observations on every routine rotation or automatically trust them after compromise.

Validator-key rotation must use a supported coordinated validator update or authenticated successor procedure with signing-state protection. Application-key rotation is not evidence that validator rotation works.

### Managed signer integration

Provide a typed signer interface and at least one real non-exporting signing-service integration. A concrete suitable target is **Vault Transit Ed25519**, subject to checking and pinning current documented support.

- Sign the exact canonical domain-separated message, not an accidental hex string or differently prehashed message.
- Bind the public key and provider key version to the committed identity.
- Avoid `latest` key-version ambiguity during a rotation.
- No private-key export or plaintext backup is required for normal operation.
- Transport/authentication, timeouts, unavailable service, wrong key, wrong algorithm, and rotation have tests.
- Use a real disposable local service for its integration gate; a fake HTTP client alone is insufficient.
- A local development Vault proves API integration, not hardware-backed key custody or secure production Vault operation.

Do not claim AWS/Azure/HSM support without a concrete implemented, tested adapter with the required signature algorithm. Keep external service deployment/operator responsibility visible.

### Client access and confidentiality

Provide authenticated mission-scoped roles for inspection, submission, approval, verification, revocation, and operations. Define actual permissions in one auditable policy source; do not allow the LLM to assert its own roles.

Use the existing OAuth resource-server support where possible, with issuer, audience, expiry, required scope, and key-rotation handling. No new authorization-server implementation is required. Add secure proxy/mTLS deployment support and explicit bearer-secret handling.

Enforce equivalent policy at A2A, MCP, artifact download, pagination, notifications, and callbacks. Test cross-mission and cross-client denial. Protect callback credentials at rest and redact them from responses/logs.

**Confidentiality limit:** validators replicate the shared application state. Gateway ACLs do not hide that state from validator operators. Define the operational trust group accordingly. Confidential compartments require separate deployments/storage scopes; do not advertise cryptographic multi-tenant privacy.

---

## 9. Add one controlled external effect: GitHub draft PR

Implement an optional `EffectAdapter` boundary and one concrete effect: opening a **draft pull request** for an accepted repository patch in an explicitly allowed repository.

Do not support automatic merge, direct pushes to protected branches, force pushes, deletion of user branches, production deployment, arbitrary HTTP requests, or secret/permission changes.

### Durable effect contract

Use a versioned state machine such as:

prepared -> authorized -> dispatch_reserved -> dispatched -> observed -> reconciled

with separate denied, expired, unknown, and compensation-required states as appropriate. Specify exact transition names in schemas and tests rather than treating this illustrative list as sufficient.

Bind the effect to:

- Operation ID, chain/mission, actor and authority revision.
- Repository identity, base commit, exact approved patch and result tree.
- Permitted head-branch namespace and draft-only action.
- Relevant policy/checker evidence, expiry, resource limit, and fence.

Recheck current eligibility and authorization at dispatch. Protect against changing arguments after approval and against stale state during failover.

A separate executor holds a least-privilege GitHub App installation token or equivalent approved credential. Generated code, gateways, and verifiers receive no token.

### CI hazard

Creating a draft PR or pushing its branch can trigger destination CI. “Draft” does not make candidate code harmless. Before enabling this adapter, require an operator-reviewed destination policy that prevents candidate-controlled code from receiving repository secrets or privileged CI credentials. Protect `.github/`, build/test control files, and the allowed patch scope.

Do not execute the candidate in `pull_request_target`, a privileged `workflow_run`, or a persistent sensitive runner. The publication pipeline and the candidate-testing pipeline must be separate trust contexts.

### Idempotency and unknown outcomes

Use deterministic operation identities and owned branch names, conditional/create-if-absent behavior where actually supported, and a serialized dispatch claim. Inspect the existing branch/PR against exact expected content before treating it as the result of a retry.

GitHub PR creation is not assumed to expose a universal exactly-once primitive. After an ambiguous reply, reconcile the original operation rather than create a fresh ID or resend blindly. Account for delayed remote visibility and concurrent requests. Preserve `unknown` when absence has not been established reliably.

A fence in CheckedFlow cannot cancel an already in-flight GitHub request. State the exact guarantees and test the failure windows. If the adapter cannot safely resume an ambiguous operation, require explicit authorized reconciliation.

Compensation may close an adapter-owned draft PR only under the declared policy and exact identity checks. It cannot erase notifications or claim that the original action never happened. Never delete human-modified content automatically.

Test against a local protocol-faithful fake server for exhaustive failure paths and, separately, an explicitly authorized disposable real repository for the supported external-provider claim. If no disposable target is authorized, keep the live gate blocked and the feature disabled; do not substitute a mock PASS for it.

---

## 10. Supervision, capacity, and emergency controls

Implement a persistent worker supervisor with bounded concurrency, backoff/jitter outside consensus, current-fence checks, and explicit heartbeat support where appropriate.

Use one active command-submission/nonce coordinator per signing identity. Prefer separate worker identities for independent workers. Do not introduce racing heartbeats and result submissions that consume the same actor nonce incorrectly.

Do not automatically rerun already-started uncertain work. Classify retryable failures and enforce finite retry/time/budget policies. Reuse the same logical operation identity for reconciliation; new IDs are not a recovery technique.

Keep verification capacity protected. Apply backpressure before generation exhausts verification/storage capacity. Track reservation, commitment, and actual observations separately.

Provide mission-level pause, drain, resume, and local emergency-disable controls:

- Pause blocks new dispatch, not historical reads.
- Drain lets already safe work settle under a declared policy.
- Local emergency disable can stop the operator's dispatcher without quorum, but cannot fabricate a consensus revocation or authorize new work.
- Resume requires current policy, unresolved-effect checks, and appropriate approvals.
- A stopped block height is not a wall-clock lease extension.

During quorum outage or stale own-node state, stop new external dispatch based on a documented local freshness policy. Local watchdog clocks may inhibit execution; they must not alter deterministic consensus time or create authority. In-flight effects remain explicitly uncertain where necessary.

---

## 11. Observability and operational objectives

Add structured logs, optional OpenTelemetry spans, Prometheus-compatible metrics, and role-aware `/healthz` and `/readyz` or equivalent endpoints.

Distinguish process liveness from readiness to accept/dispatch protected work. Readiness should reflect required configuration, own-node freshness, storage, signer availability, sandbox readiness for executing roles, and maintenance state without leaking secrets.

At minimum expose:

- Committed height, lag/freshness, rejected commands by bounded reason.
- Queue depth, active leases, expired leases, uncertain tasks.
- Verification backlog and time-to-checked completion.
- Open residuals and oldest age under an explicit time basis.
- Artifact availability failures and storage usage/headroom.
- Spent/reserved/available modeled budget; separate observed metering if present.
- Effect dispatch/unknown/reconciliation counts.
- Signer, backup, snapshot, restore, callback, and protocol errors.

Do not use task IDs, content digests, customer text, tokens, or arbitrary URLs as unbounded metric labels. Put high-cardinality identifiers in protected logs/traces. Prevent replay or monitor restart from double-counting authoritative service events.

Tracing context is untrusted correlation metadata, not authorization. Exporters must be optional, bounded, and unable to affect consensus. Default to no outbound telemetry until the operator configures it.

Supply a small dashboard/alert configuration and runbook mapping, not just metric names. Include alerts for no quorum, unsafe state headroom, unbacked reservations, unresolved-effect age, missing backup, and expired credentials.

Document SLI definitions, denominators, operating envelope, RPO/RTO targets, recovery conditions, and error-budget actions. Publish only measured outcomes; targets are not achieved SLOs, contractual SLAs, or long-duration reliability evidence.

---

## 12. Deployment paths without hiding operational requirements

Implement:

1. A local disposable Compose-based laboratory, using the already supported runtime or a justified equivalent.
2. A multi-host inventory/configuration generator and supervised service deployment for four pre-provisioned Linux hosts, for example through systemd and bounded idempotent deployment scripts.

Do not provision machines or change network/firewall rules without an explicitly authorized inventory. Do not configure a permanent Docker daemon automatically. Root-level installation steps must be explicit and confined to disposable/approved hosts.

Separate commands for **plan/preflight**, **apply/start**, **status**, **drain**, **stop**, **backup**, **restore**, and **qualification**. Command names are design proposals: introduce and test them rather than documenting nonexistent commands.

The default must not expose RPC, ABCI, management, artifact, or metrics services publicly. Configure authenticated transport for cross-host services, restrict P2P peers, and pin images/binaries with provenance and hash checks.

Do not ship shared production keys, fixed passwords, copied validator signing state, development CA trust, or writable source checkouts as deployment defaults.

Provide expected prerequisites and measured resource use from actual tests. Do not promise a five-minute installation or an unmeasured low hardware requirement.

One-command laboratory setup is not one-command enterprise deployment. Make this distinction visible.

---

## 13. Required qualification matrix

Create an executable machine-readable matrix, with exact test IDs, requirements, artifact identities, environment dimensions, results, and log references.

### G1 — Core, contracts, and legacy replay

Run deterministic/property tests, schema vectors, strict typing/linting, dependency-direction checks, and selected invariant mutation tests.

Preserve existing baseline tests and all legacy signed vectors. Keep at least the current minimum coverage gates for the core; extend coverage to newly authoritative admission, migration, effect, and identity code. Document justified exclusions; do not inflate coverage with trivial wrappers.

Mutations must target skipped authority, missing fence, omitted dependency checks, uncharged retries, duplicated service, weak snapshot trust, bypassed patch checks, and missing cross-scope controls.

### G2 — Installed artifacts and real single-host infrastructure

Test the exact built wheel and sdist in fresh environments outside the checkout, with recorded import origin. Preserve the supported Windows/Linux and Python-version matrix unless an explicitly justified compatibility decision is documented.

Run existing real CometBFT/gVisor cases, not replacements. Extend them for the new domain, state profile, content store, key service, and protocol projections.

### G3 — Practical patch and external-effect tests

Exercise a real patched fixture project and output checks in gVisor. Include invalid code, test tampering, report forgery, zero/omitted tests, prohibited paths, dependency drift, and malicious fixture behavior.

Run real S3-compatible storage and real supported signer-service tests in disposable infrastructure. Exercise corruption, partial upload, availability, scope, and rotation—not just happy paths.

Exhaustively exercise effect failure windows against the fake provider. The authorized real draft-PR smoke is a separate explicit gate with its exact target and resulting object identity.

### G4 — Recovery and lifecycle

Test:

- Worker/ABCI crash before and after commit.
- Loss of a reply after a successful operation.
- Pause, drain, stale node, and quorum outage.
- Snapshot export/import, invalid snapshots, and caught-up restoration.
- Archival/rollover and old-request replay after retirement.
- Late dependency/verification withdrawal and residual reopening.
- Normal key rotation, compromised-key revocation, and node replacement.
- 0.1-to-0.2 migration with unresolved work.
- Compatible rolling upgrade and incompatible-version rejection.

### G5 — Bounded load and longevity

Use deterministic workloads, fixed arrival patterns, a specified machine envelope, finite time, and cleanup. Cross old state limits without losing governance capacity or history semantics.

Measure latency distributions, queue growth, memory/disk use, bytes per committed task, recovery duration, and drain behavior. Separate clean acceptance throughput from completed verified-work throughput.

Do not repeatedly alter the workload after seeing failures to manufacture a passing claim. Changes to the declared workload require a new run record.

### G6 — Real multi-host operational qualification

Use four separate approved Linux node environments. Separate VMs demonstrate distributed fault domains but do not, by themselves, demonstrate independence of administrative organizations.

Run real communication loss, one-node interruption, two-node quorum loss, delayed replies, storage failure, key-service interruption, node replacement, backup restoration, and upgrade cases under bounded controlled conditions.

Do not call four containers on one host multi-host evidence. Record host topology truthfully without publishing private IPs/credentials.

If unavailable, produce the harness and mark G6 BLOCKED. Do not call the overall operational profile qualified.

### G7 — Release-security checks

Review dependency advisories, supply-chain pins, signer exposure, CI trust boundaries, secret/path leakage, archive safety, scope isolation, and documented deployment assumptions.

Automated scans are not an independent penetration test. Preserve adverse findings and limitations. Blocking high-risk findings must be resolved or the release must remain blocked; do not silence the rule globally.

### Evidence rules

Every PASS needs an actual test result tied to the checked source/artifacts. Missing, skipped, failed, errored, or stale mandatory tests block the corresponding gate.

Synthetic generation is acceptable for engineering tests. It does not establish LLM quality, economic benefit, real organizational independence, or causal collective acceleration.

---

## 14. Milestones and resumable implementation

Work in reviewable increments within the final 0.2.0 effort:

| Milestone | Required deliverable | Dependency |
|---|---|---|
| M0 | Baseline, threat model, operational envelope, ADRs, acceptance matrix | None |
| M1 | Versioned core contracts, legacy replay, capacity/governance design | M0 |
| M2 | Artifact storage, archival/checkpoints, snapshot and migration mechanisms | M1 |
| M3 | Repository-patch domain and independent verification path | M1–M2 |
| M4 | Role-separated identity, access policy, real managed signer and rotation | M1–M2 |
| M5 | Supervision, budgets, external draft-PR effects, recovery | M2–M4 |
| M6 | Observability, local and multi-host deployment/runbooks | M2–M5 |
| M7 | Actual installed, fault, longevity, multi-host and security qualification | M3–M6 |
| M8 | Stable-contract documentation and final 0.2.0 release gates | M7 |

Do not merely commit a stub for each milestone. Implement the vertical path and its rejection/recovery cases.

If the session cannot finish everything, commit a coherent safe increment, update the implementation ledger with precise remaining tasks, and provide a resume instruction. Do not relabel partial work as complete or publish because a session is ending.

Progress through unblocked milestones without repeatedly asking for approval of ordinary code changes. Request only genuinely missing execution/publication permissions, infrastructure, credentials, or policy decisions that cannot be inferred safely.

---

## 15. Compatibility, documentation, and language-independent contracts

All new public documentation, comments, schemas, operator messages, and delivery reports should be in clear English.

Update README, tutorials, API references, architecture, protocol, security, operations, conformance, releasing, migration, and agent-facing entry points. Preserve research attribution and non-claims.

Provide:

- What `repository-patch/v1` supports and excludes.
- Which exact tests constitute acceptance, and what they do not establish.
- Which services/operators remain trusted.
- Per-command read/write/network/external-effect behavior.
- Stable public SDK/CLI surfaces and schema compatibility rules for 0.2.x.
- Explicit deprecation policy and upgrade matrix.
- Machine-readable errors, retryability, required operator action, and unresolved statuses.
- Python examples, JSON schemas, canonical vectors, and a language-independent description sufficient for a later port.
- Common incident and recovery procedures, including “do not do this” examples.
- Separate availability, correctness, acceptance, and current-usability status.

Do not announce a stable SemVer 1.x ecosystem while publishing 0.2.0. State the actual compatibility commitment for the supported 0.2.x public interfaces and versioned wire profiles.

Keep old evidence records intact. Add new dated reports rather than overwriting the 2026-09-25 validation report with new numbers.

Do not edit the Collective Intelligence Index or companion repositories. Provide a factual metadata/update summary for a later Index task.

---

## 16. Release process: 0.2.0 only, exact tested artifacts

Preserve the existing manual Trusted Publishing identity unless it is independently changed through an authorized process:

- PyPI project: `checkedflow`
- Repository: `kadubon/checkedflow`
- Workflow file: `.github/workflows/workflow.yml`
- Publisher environment: `pypi`
- OIDC permission only on the publication job

The current repository requires explicit publication authorization and exact-artifact qualification. This task authorizes only the final 0.2.0 release after the declared mandatory gates pass.

### Secure CI

- Pin third-party Actions and external runtimes to verified immutable identities.
- Give ordinary builds/tests no publishing credential or long-lived secret.
- Run untrusted candidates only in disposable isolated test environments.
- Never execute fork-controlled code with secrets through `pull_request_target` or a privileged workflow chain.
- Do not expose persistent sensitive self-hosted runners to public pull requests.
- Use protected, explicitly authorized workflows for tests needing managed infrastructure or disposable-provider credentials.
- Do not bypass required reviews or environmental approvals.

### Build and gate

After final source/documentation edits, build the wheel and sdist once for the release candidate. Record source SHA and both hashes. Distribute those exact bytes to installed-artifact and infrastructure tests.

A source-checkout test is not evidence for a different rebuilt wheel. Tests must record import origin and reject accidental imports from the checkout.

For multi-host/external qualification that cannot run in the ordinary matrix, extend the release gate to verify a protected workflow result bound to the same source SHA and exact wheel/sdist hashes. Do not trust a user-editable JSON file that merely says PASS. Preserve the current same-artifact and freshness guarantees.

Store final run reports as CI/release evidence artifacts, not by editing the packaged source after qualification. If packaged content changes, rebuild and requalify. Avoid circular/self-referential “this artifact contains its own final hash” records.

The final gate must check completeness of M0–M8 requirements and G1–G7 evidence, required approvals, tag/version alignment, distribution contents, compatibility fixtures, vulnerability/leakage checks, and declared operational-profile qualification.

### Publish and independently inspect

After normal-policy merge and successful final qualification:

1. Create immutable-by-policy tag `v0.2.0` at the approved source.
2. Use the authorized manual workflow/publisher path; no ad hoc token upload.
3. Publish those exact distributions with supported PyPI attestations.
4. Attach the same bytes, checksums, SBOM if generated, source reference, qualification manifest, limits, and migration notes to the GitHub release.
5. Fetch public release and PyPI metadata, verify distribution hashes and provenance against the expected publisher identity.
6. Install the public wheel and sdist separately into fresh environments outside the checkout. Verify version, packaged schemas/resources, CLI, SDK, base/optional dependency boundaries, and bounded smoke behavior.
7. Report publication and post-publication installation as separate statuses.

Existing v0.1.0 artifacts and tags remain unchanged. An already existing 0.2.0 is a conflict requiring owner direction, not permission to overwrite or increment silently.

If publication is blocked, leave a ready PR and complete release handoff. Do not publish a release with missing mandatory infrastructure evidence merely because it carries a pre-1.0 number.

---

## 17. Final delivery report

Return an English report with:

1. Exact source/merge/tag identities and package version.
2. The supported operational profile and explicit exclusions.
3. Implemented milestones, with file/module references.
4. Legacy compatibility and protocol/state migration results.
5. Practical patch-domain evidence and its assurance scope.
6. Artifact store, capacity/archival, snapshot/restore, key rotation, effect, and supervision results.
7. Real versus simulated versus source-only versus blocked integrations.
8. Exact test matrix, case counts, resource envelope, failures, fixes, and reruns.
9. Measured latency/recovery/storage results where actually available; no invented SLO attainment.
10. Multi-host topology, test scope, and remaining operator-independence limitation.
11. Residual risks, current trust assumptions, and unresolved security/operational obligations.
12. GitHub/PyPI publication status, public artifacts, hashes, attestations, and public-install verification.
13. No paid LLM calls, model training, unauthorized cloud spend, or production-target writes.
14. No changes to the Index or companion repositories.
15. A concise next-action list only for genuine blockers—not a claim that required work is complete when it is not.

Use precise status language:

- IMPLEMENTED
- TESTED_IN_SOURCE
- TESTED_AS_INSTALLED_ARTIFACT
- QUALIFIED_SINGLE_HOST
- QUALIFIED_MULTI_HOST
- LIVE_EFFECT_QUALIFIED
- RELEASE_READY
- PUBLISHED
- PUBLIC_ARTIFACT_VERIFIED
- BLOCKED

Do not compress them into “all done.”

---

## 18. Engineering references to inspect at implementation time

The repository's pinned source and contracts are primary. Read current official documentation and pin the actual supported versions; do not assume these general links describe every version identically.

- CheckedFlow protocol: https://github.com/kadubon/checkedflow/blob/main/docs/protocol.md
- CheckedFlow state machine: https://github.com/kadubon/checkedflow/blob/main/docs/state-machine.md
- CheckedFlow operations: https://github.com/kadubon/checkedflow/blob/main/docs/operations.md
- CheckedFlow release process: https://github.com/kadubon/checkedflow/blob/main/docs/releasing.md
- CometBFT documentation: https://docs.cosmos.network/cometbft/latest/docs/README
- CometBFT state-sync concepts (verify against the pinned runtime): https://docs.cosmos.network/cometbft/v0.38/docs/core/state-sync
- gVisor security model: https://gvisor.dev/docs/architecture_guide/security/
- GitHub Actions secure use: https://docs.github.com/en/actions/reference/security/secure-use
- GitHub pull-request trust boundary: https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target
- Vault Transit API: https://developer.hashicorp.com/vault/api-docs/secret/transit
- Prometheus instrumentation: https://prometheus.io/docs/practices/instrumentation/
- OpenTelemetry Collector: https://opentelemetry.io/docs/collector/
- PyPI Trusted Publishing: https://docs.pypi.org/trusted-publishers/
- PyPI digital attestations: https://docs.pypi.org/attestations/
- PyPI provenance verification: https://docs.pypi.org/attestations/consuming-attestations/

## Final principle

Do not make CheckedFlow operational by relaxing what its evidence means.

Make it operational by giving the existing distinctions durable implementations:

**a useful task, a controlled execution boundary, appropriate checks, explicit authority, sustainable state, recoverable history, observable operation, and a tested upgrade path.**

Deliver that work as **CheckedFlow 0.2.0** only when its stated release conditions are actually satisfied.
