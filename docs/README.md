# Documentation index

Read [concepts](concepts.md) first to understand the vocabulary, then follow the
[tutorial](tutorial.md). The public [README](../README.md) gives the shortest runnable entry points.

| Document | Question answered |
|---|---|
| [Concepts](concepts.md) | What is a checked capability, and which guarantees follow from it? |
| [Tutorial](tutorial.md) | What can I run locally, and what should its output mean? |
| [Architecture](architecture.md) | Which component owns each decision and effect? |
| [State machine](state-machine.md) | Which transitions, bounds and accounting rules are enforced? |
| [Governed external operations](work-effects.md) | How are provider intent, dispatch authority, uncertainty and reconciliation separated? |
| [Supervised effect dispatch](effect-dispatch.md) | How are current operator policy, stored evidence and node freshness checked immediately before a draft request? |
| [Durable effect supervision](effect-supervision.md) | How does an authorized draft operation survive crashes and lost reporting replies without being executed twice? |
| [Approved Git staging](git-staging.md) | How are exact commits created under explicit policy, and why are interrupted writes never resumed? |
| [Protocol](protocol.md) | Which bytes are signed and how are commands replayed? |
| [Adapters](adapters.md) | How do storage, generators, workers and sandboxes connect? |
| [Agent communication](interoperability.md) | How do A2A and MCP clients discover, submit and inspect the same work? |
| [Conformance](conformance.md) | Which protocol operations and application constraints are tested? |
| [Security](security.md) | Which trust boundaries, leakage checks and deployment limits were reviewed? |
| [Operations](operations.md) | How are four nodes provisioned, supervised and recovered? |
| [Porting](porting.md) | What must an implementation in another language reproduce? |
| [Research](research.md) | Which of the 37 sources motivated each implemented principle? |
| [Verification](verification.md) | What does each test gate establish? |
| [Validation status](validation-status.md) | Which checks actually ran, on which environments? |
| [Plan audit](audit.md) | Which original requirements are implemented, qualified or limited? |
| [Releasing](releasing.md) | How are the exact tested files prepared for Trusted Publishing? |
| [0.2 implementation](implementation-0.2.0.md) | What is being implemented and which gates remain open? |
| [Proposed operational profile](operational-profile-0.2.md) | What workload and deployment must qualify before 0.2 release? |
| [Four-VM experiment](vm-laboratory-2026-09-26.md) | What was observed on separate guest kernels, and which operational cases remain unqualified? |
| [Repository patch boundary](repository-patch.md) | Which patch admission primitives exist and what remains unintegrated? |
| [Operational identity](operational-identity.md) | How do v2 signed bytes separate key purposes, revisions and organization approvals? |
| [Key lifecycle](key-lifecycle.md) | How are application keys scheduled, proven, activated, retired and revoked? |
| [Managed signer](managed-signer.md) | How does pinned Vault Transit signing bind exact bytes to the committed public key? |
| [Operational control](operational-control.md) | How do initial v2 control transitions and bounded epoch receipts behave? |
| [Operational consensus](operational-consensus.md) | How do the separate v2 ABCI service and own-node client commit and recover state? |
| [Work acceptance](work-acceptance.md) | How do funded checks, quorum, late contradictions and withdrawal affect acceptance? |
| [Repository reuse](repository-reuse.md) | How are exact accepted bytes fetched and checked, and why does a new base require new qualification? |
| [Worker supervision](worker-supervision.md) | How do durable commands, one bounded invocation and conservative interruption recovery connect? |
| [Worker scheduling](worker-scheduling.md) | How do call limits, deadlines and emergency stop survive process restarts? |
| [Operational agent transports](operational-agents.md) | How do A2A and MCP expose native v2 records and preserve original signed bytes? |
| [Callback credential custody](callback-secrets.md) | How are callback secrets encrypted, redacted, rotated and recovered? |
| [Sandbox recovery](sandbox-recovery.md) | How are owned containers recovered after a worker is killed, and what remains uncertain? |
| [Settled-work retirement](work-archive.md) | How can completed records leave active state without resetting costs or unresolved obligations? |
| [Work tasks](work-tasks.md) | How do approved tickets, worker leases, fences and uncertain outcomes connect? |
| [Work budget](work-budget.md) | How are bounded funding reservations, verification allocations and uncertain charges governed? |
| [Operational storage](operational-storage.md) | How are local control state, signed block history and archive batches committed atomically? |
| [Artifact storage](artifact-storage.md) | How are bounded artifact bytes stored and checked separately from authorization and availability? |
| [S3 storage](s3-storage.md) | How do conditional writes, verified reads, lost replies and real-service qualification work? |
| [Retention](retention.md) | How do persistent roots, grace periods and tombstones protect bytes during reclamation? |
| [Retention backups](retention-backup.md) | How are catalog snapshots restored without trusting their own metadata or overwriting a live store? |
| [Application backups](application-backup.md) | How are signed block histories verified and replayed into a new database without restoring validator authority? |
| [GitHub draft provider](github-drafts.md) | How are exact pre-staged drafts created once locally and reconciled without automatic resend? |
| [Dispatch watchdog](dispatch-watchdog.md) | Why can a responsive node still be unsafe for starting new work, and how is dispatch inhibited? |

For agents, [skills.md](../skills.md) provides the reading order, machine contracts and validation
commands. Schemas describe structural contracts; transition rules also impose semantic invariants.
Tests and historical reports are evidence about a particular implementation, not a replacement for
those contracts. Read the limitation beside a research or validation claim before reusing it.

- [Client access](client-access.md): mission/client roles, verified identities, callback ownership and live revocation.

- [Authorized downloads](artifact-download.md): shared A2A/MCP policy for explicitly published current and archived bytes.

- [Agent TLS](agent-tls.md): mutual TLS, explicit proxy advertisements and credential retirement.

- [Effect reconciliation](effect-reconciliation.md): historical GET-only evidence and unsigned quorum-review proposals.

[Replicated artifact access](replicated-artifacts.md) requires fresh verified reads
from at least three of four configured backends. Availability observations are not acceptance
or future-use authorization; replica placement must be qualified independently.

[Service observations](observability.md) distinguish process liveness, readable state
and protected-work readiness. Optional HTTP status routes remain inside mission authentication;
current-state gauges do not count a replayed event again.

Local worker/effect/service invocations can opt into [bounded operation logs and optional traces](telemetry.md).
These observations are lossy diagnostics, not committed events, task acceptance or execution authority.
No outbound exporter is enabled by default.

Use the [monitoring templates and response runbook](monitoring-runbook.md) for packaged alert rules,
dashboard queries, explicit SLI denominators and recovery targets. Templates and targets are not
proof of an installed monitoring service or achieved availability.

[Legacy migration preparation](legacy-migration.md) authenticates and retains old state and
prepares conserved successor accounting. It does not activate a node or retire the old validator.
