# Documentation index

Read [concepts](concepts.md) first to understand the vocabulary, then follow the
[tutorial](tutorial.md). The public [README](../README.md) gives the shortest runnable entry points.

| Document | Question answered |
|---|---|
| [Concepts](concepts.md) | What is a checked capability, and which guarantees follow from it? |
| [Tutorial](tutorial.md) | What can I run locally, and what should its output mean? |
| [Architecture](architecture.md) | Which component owns each decision and effect? |
| [State machine](state-machine.md) | Which transitions, bounds and accounting rules are enforced? |
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
| [Repository patch boundary](repository-patch.md) | Which patch admission primitives exist and what remains unintegrated? |
| [Operational identity](operational-identity.md) | How do v2 signed bytes separate key purposes, revisions and organization approvals? |
| [Key lifecycle](key-lifecycle.md) | How are application keys scheduled, proven, activated, retired and revoked? |
| [Managed signer](managed-signer.md) | How does pinned Vault Transit signing bind exact bytes to the committed public key? |
| [Operational control](operational-control.md) | How do initial v2 control transitions and bounded epoch receipts behave? |
| [Operational consensus](operational-consensus.md) | How do the separate v2 ABCI service and own-node client commit and recover state? |
| [Work acceptance](work-acceptance.md) | How do funded checks, quorum, late contradictions and withdrawal affect acceptance? |
| [Repository reuse](repository-reuse.md) | How are exact accepted bytes fetched and checked, and why does a new base require new qualification? |
| [Work tasks](work-tasks.md) | How do approved tickets, worker leases, fences and uncertain outcomes connect? |
| [Work budget](work-budget.md) | How are bounded funding reservations, verification allocations and uncertain charges governed? |
| [Operational storage](operational-storage.md) | How are local control state, signed block history and archive batches committed atomically? |
| [Artifact storage](artifact-storage.md) | How are bounded artifact bytes stored and checked separately from authorization and availability? |
| [S3 storage](s3-storage.md) | How do conditional writes, verified reads, lost replies and real-service qualification work? |
| [Retention](retention.md) | How do persistent roots, grace periods and tombstones protect bytes during reclamation? |
| [Retention backups](retention-backup.md) | How are catalog snapshots restored without trusting their own metadata or overwriting a live store? |

For agents, [skills.md](../skills.md) provides the reading order, machine contracts and validation
commands. Schemas describe structural contracts; transition rules also impose semantic invariants.
Tests and historical reports are evidence about a particular implementation, not a replacement for
those contracts. Read the limitation beside a research or validation claim before reusing it.
