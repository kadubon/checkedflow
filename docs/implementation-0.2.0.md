# CheckedFlow 0.2.0 implementation record

Status: **IN PROGRESS; NOT RELEASE READY**. Package 0.1.0 remains the published baseline.
The only release authorized by this effort is 0.2.0 / `v0.2.0`.

## Baseline inspected on 2026-09-26

- Local and remote main: `a58869e2488bed9550b006d261601240357b2b99`.
- Published release source: `cbe6ad8670a8936683d911d5fe216a94fc3a1fff`.
- GitHub repository is public; default branch is main. The ruleset listing is empty and main
  reports `protected: false`. This observation does not authorize bypassing future policies.
- GitHub and PyPI list only 0.1.0; no remote `v0.2.0` tag was present.
- Working tree was clean. Development branch: `feat/0.2-operational-hardening`.
- Existing runtime is v1, with four organizations and the finite integer-array domain.
- Existing source checks are rerun in a disposable uv environment. Old validation reports are
  retained unchanged; they do not qualify new source or a new distribution.

## Requirements and decisions

The [frozen task specification](specification-0.2.0.md) is the requirement source, not executable
instructions for gateways or candidate programs. The [implementation ledger](implementation-0.2.0.json)
records individual requirement paragraphs with stable identifiers and source line references.
An implementation status is not a release approval. Evidence must identify actual code, tests,
source revision, artifact hashes, environment and executed results before qualification.

The initial import retained every nonempty source paragraph, including reference links and
reporting instructions. Those 377 records are not equally sized engineering requirements and must
not be used as a completion percentage. An audit found that their statuses remained `NOT_STARTED`
despite separate component records. The initial traceability audit linked twenty components to 88 paragraphs,
with reciprocal evidence references and `IN_PROGRESS` status. This deliberately does not promote
partial component evidence to complete paragraph acceptance. Read each component's scope and the
remaining milestone obligations. Offline static checks validate frozen text/line/hash identity,
unique IDs, reciprocal references and local implementation/test/document paths. They do not
authenticate a test result or authorize publication; G1–G7 remain unqualified. Subsequent components
add reciprocal links without converting partial implementation into completed requirements.

1. Preserve the v1 interpreter and signed bytes; add a separate v2 profile. See
   [the protocol boundary ADR](adr-0001-versioned-operational-state.md).
2. Use bounded active state and explicit epoch retirement, with retained authenticated history.
   See [the archive ADR](adr-0002-bounded-active-state.md).
3. Implement storage and identity as adapters; consensus never performs network or filesystem I/O.
4. Introduce a restricted repository patch contract and independently interpreted output checks.
   A test process importing candidate code cannot grant its own acceptance.
5. Keep external GitHub effects disabled until destination policy, explicit target authorization,
   and a live qualification run exist. A credential is not target authorization.

## Milestones and acceptance dependencies

| Milestone | Deliverable | Dependency | Current state |
|---|---|---|---|
| M0 | Baseline, envelope, threat boundaries, ADRs, requirement ledger | None | IN PROGRESS |
| M1 | Separate v2 contracts, legacy replay, administrative reserve | M0 | IN PROGRESS |
| M2 | Local/S3 storage, archive, checkpoints, snapshots, migration | M1 | IN PROGRESS |
| M3 | Repository patches and independent bounded verification | M1–M2 | IN PROGRESS |
| M4 | Role separation, scoped access, managed signer, rotation | M1–M2 | IN PROGRESS |
| M5 | Worker supervision, effect journal, recovery | M2–M4 | IN PROGRESS |
| M6 | Metrics, readiness, deployments, runbooks | M2–M5 | NOT_STARTED |
| M7 | Installed, infrastructure, fault, load, multi-host qualification | M3–M6 | IN PROGRESS |
| M8 | Documentation, exact-artifact publication gates, release | M7 | IN PROGRESS |

## Infrastructure dependencies and execution limits

At baseline, no four-host inventory or disposable GitHub effect target had been supplied.
The owner subsequently authorized both. The disposable GitHub component test is recorded below;
four local Linux VMs now have actual installed-wheel component fault observations in the
[dated VM report](vm-laboratory-2026-09-26.md). Infrastructure availability no longer blocks this
work. Full G6 and end-to-end effect qualification remain incomplete; neither mocked tests nor
four colocated containers can satisfy them.
No paid resources, model calls, model downloads or production-target writes are authorized.

Initial source baseline: one Windows Python 3.12 run, at most 15 minutes, no candidate execution.
The command is `uv run python scripts/check.py --actionlint PATH_TO_ACTIONLINT` in a disposable
uv project environment. Logs are ignored local evidence under `reports/baseline-0.2.log`.
Each subsequent infrastructure stage must declare its dimensions and finite timeout before running.

## Completed foundation work

- Baseline source checks: 158 tests passed, eight infrastructure cases deliberately deselected,
  all existing static checks passed, all five existing invariant mutations detected.
- [Frozen published-runtime capture](legacy-capture-0.1.md): 1,038 blocks and 43 checkpoints;
  four added compatibility tests passed. A subsequent full check passed 162 source tests.
- Local development wheel and sdist containing that capture passed installed-package checks
  in fresh environments outside the checkout. These are unpublishable development builds with
  the baseline 0.1.0 metadata, not the final 0.2.0 candidate or a replacement for public 0.1.0.
- [Repository patch admission](repository-patch.md): immutable tree/preimage/result bindings,
  portable path rejection and independent bounded output comparison. Seventeen focused tests
  passed. End-to-end sandbox/domain integration is still pending.

No G1–G7 gate is promoted by these partial results. New primitives still require broader mutation,
installed-artifact and actual infrastructure qualification. M1–M8 remain incomplete.

The combined foundation check subsequently passed 179 source tests in 34.49 seconds on Windows
Python 3.12.10, with eight infrastructure cases deselected. Ruff, strict mypy, Bandit, architecture,
schema/link checks, actionlint and the 152-member publication scan passed; five legacy mutations
were detected. This is a source-only result for the new domain primitives. The development artifact
checks above predate that module and qualify only the explicitly recorded capture increment.

The first draft-PR CI run uses the existing finite workflow: a 20-minute build ceiling, six
Windows/Linux Python 3.12–3.14 check jobs capped at 25 minutes each, and one fresh Linux
CometBFT/gVisor qualification job capped at 30 minutes. These existing cases qualify the legacy
profile only. They cannot satisfy new S3, managed-signer, patch execution or multi-host gates.

## Identity-boundary increment

The [v2 identity boundary](operational-identity.md) adds explicit purpose, mission, revision,
height and four-organization membership checks. It signs under a distinct domain and accepts
only original envelope bytes. Parsed SDK objects, old epochs and v1 signatures are rejected.
It does not yet implement v2 transitions, nonce persistence, rotation governance or managed custody.

On 2026-09-26 the updated source check passed 193 tests in 34.88 seconds on Windows Python 3.12.10,
with eight infrastructure cases deselected. All static checks and ten invariant mutations passed.
The identity and patch-admission modules each reached 100% statement and branch coverage in this
suite. Four identity-module lines are type-only `Protocol` declarations excluded by coverage's
default typing rules; no runtime admission code is excluded. Coverage is a test scope metric,
not a correctness proof. The legacy core keeps its independent 95% minimum thresholds.

The [first draft-PR run](https://github.com/kadubon/checkedflow/actions/runs/36204053007) succeeded
for source `6bb511000400642708972fdbc48ea65b77570175`: build, all six Windows/Linux Python matrix
jobs and existing real infrastructure qualification. It predates this identity increment and
cannot qualify it. Publication was skipped as expected for a draft PR.

Local development wheel/sdist authentication smoke checks also succeeded before the final
original-bytes type guard was added. Those checks are preliminary; the next PR run must build
and test the final increment. None of these development artifacts is authorized for publication.

## Next implementation order

The [Vault Transit signer](managed-signer.md) now implements exact-message signing with an explicit
provider version and committed public-key binding. Five protocol-server cases cover TLS, timeouts,
malformed responses, secret redaction and authority mismatch. A separate real Vault 2.1.1 process
case passed on Windows Python 3.12.10, including TLS certificate validation, non-export policy,
least-privilege denial, provider rotation, governed CheckedFlow rotation, token revocation and outage.
The combined focused run passed six cases in 11.02 seconds. The final source suite passed 252 tests
in 47.80 seconds, all static checks and nineteen selected mutations; nine infrastructure/service
cases were deselected in that source-only run. The real signer case was executed separately.
The adapter reached 100% statement/branch coverage in its focused protocol suite.

The installed-wheel qualification script and Linux CI job are implemented with pinned binary checks,
fresh environment import verification and finite process-tree cleanup. Their final artifact/CI
results must be inspected separately; source service success alone does not establish them. None of
these component results qualifies the whole G4 gate, production custody or operational deployment.

The [pending-key recovery CI run](https://github.com/kadubon/checkedflow/actions/runs/36209264395)
succeeded for `6febbe6385c564b66261390a880594e1228bd7f2`. It predates the managed signer increment.

Pending-key recovery now permits a new governed revision after revoking a lost pending key,
without waiting for its planned activation. Replacement shortens or preserves older retirement
boundaries and cannot revive an expired key. Three additional lifecycle cases cover actual local
reopen/replay, bounded generated operation sequences and nonresurrection. The final source check
passed 247 tests in 39.20 seconds on Windows Python 3.12.10, all static checks and eighteen selected
mutations; eight infrastructure cases were deselected. The revised key registry reached 100%
statement/branch coverage in the focused twelve-case suite. This closes the pending-replacement
limitation recorded below; key-history archival and the other operational integrations remain open.

The [initial key-lifecycle CI run](https://github.com/kadubon/checkedflow/actions/runs/36208880655)
succeeded for `ab9a2009313ed2f031ca94e011fc03953fd5a958`. It predates pending-key recovery and cannot
qualify this later change or replace the new operational gates.

The [application-key lifecycle](key-lifecycle.md) now connects governed scheduling/revocation,
exact proposal-bound possession proofs, future-height activation, nonce continuity and persistent
signed replay. The nine lifecycle cases and existing source suite passed together: 244 tests in
43.83 seconds on Windows Python 3.12.10, eight infrastructure cases deselected. The schema cases
also reject malformed revocation reasons with protocol failures rather than Python type errors.
The final run detected all seventeen selected mutations and passed all static checks.
Key-registry and authentication coverage reached 100% statements and branches in the focused suite.
At that increment, a revoked pending revision could not be replaced before its scheduled activation;
the recovery change above removes that restriction. Complete operational rotation still requires
work-level compromise quarantine, registry archival, validator rotation and managed custody.
Retained history is currently bounded at 1,024 revisions.

The [artifact-store CI run](https://github.com/kadubon/checkedflow/actions/runs/36208135291) succeeded
for `9a1f9ed66bd0f8bfd37d44e83efeb9384c20d136`. It predates key-lifecycle changes and qualifies only
the recorded increment and existing infrastructure cases, not the new operational gates.

The [artifact reference and local byte store](artifact-storage.md) add portable typed references,
scope checks before I/O, verified bounded streams, atomic publication and per-scope quotas. Ten
local source cases cover malformed uploads, concurrent publication/capacity, corruption, removal,
rollback and schema agreement. This is a filesystem-backed SQLite component, not S3, availability
admission, retention/pinning, network authorization or full consensus/domain integration.
The combined Windows Python 3.12.10 run passed 235 source tests in 44.07 seconds; eight
infrastructure cases were deselected. Both new modules reached 100% statements and branches in
their focused source suite. All fifteen selected invariant mutations and static checks passed.
After tightening schema rejection of trailing newlines, the ten focused cases and static checks
passed again. Coverage does not establish operational correctness.

The [atomic-storage CI run](https://github.com/kadubon/checkedflow/actions/runs/36207633409) passed
for `38453811f5884cb5bd02cb75e3183d15981bbeb6`, including the existing matrix and legacy infrastructure
cases. It predates the new artifact component. Fresh development wheel and sdist installations
for that commit exercised signed control/store/replay and recorded their package import under
`Lib/site-packages/checkedflow/__init__.py`, outside the source tree. These are development builds
with 0.1.0 metadata, not authorized release candidates.

The [local control store](operational-storage.md) now atomically commits bounded state, signed
block history and request archives. Its eleven source cases cover reopen/replay, concurrent writers,
SQL rollback, actual process exit before/after commit, corruption and checkpoint mismatch. Store
failures abort the transaction rather than becoming command acceptance. State/archive schemas and
a bounded validating codec are packaged; bootstrap trust and snapshot restore are not implied.
The combined Windows Python 3.12.10 source run passed 225 tests in 38.50 seconds and thirteen
selected invariant mutations, with eight infrastructure cases deselected. Local store/codec
coverage reached 100% statements and branches in the focused suite. Existing SQLite test connections
were subsequently made explicitly closing; the eleven focused cases passed again.

The [control increment CI run](https://github.com/kadubon/checkedflow/actions/runs/36205962214)
passed for `cddd1c9d9c5890117b7bdfa753e0b6fd437fdf90`; it predates this storage increment.

The [initial v2 control runtime](operational-control.md) now connects signed admission to
pause/drain/resume and bounded request rollover. Seven control tests and fourteen journal tests
cover duplicate effects, saturation, nonce continuity, authenticated scope and retired IDs. The
fixed journal test admits 5,000 requests while retaining at most 64 active receipts; it does not
qualify full-state longevity. The full Windows Python 3.12.10 check passed 214 tests in 41.36
seconds, all static checks and twelve selected invariant mutations. Eight infrastructure tests
were deselected locally. A stale-context regression additionally rejects a previously authenticated
context after its registry credential is withdrawn.

The [identity increment CI run](https://github.com/kadubon/checkedflow/actions/runs/36204933803)
passed for `7a0d70d9274145f75fa3e606232a872310ce6ca2`. It predates these control changes. Initial
control development wheel and sdist passed fresh Windows Python 3.12 installation checks,
including signed pause and rollover, the legacy capture, CLI/SDK examples and optional-agent
discovery. Hashes are retained in the implementation ledger. These builds precede this evidence
prose and retain development 0.1.0 metadata; they must not be published. Only the final 0.2.0
source/artifact qualification can authorize release.

Use the frozen legacy captures as regression evidence. Extend the initial control profile with
governed key lifecycle and bounded work/budget/dependency state, then connect atomic archive storage
and worker execution. The journal alone does not preserve work obligations that are not yet modeled.
Update individual
ledger entries only when their evidence exists. Keep all G1–G7 gates unqualified until their exact
required cases execute against the final artifacts. Never promote the ledger itself to PASS evidence.


### Repository-tree runner increment

A separate `GVisorRunner.run_tree` path materializes bounded UTF-8 repository trees using Linux
no-follow directory descriptors inside a fresh private temporary parent. Candidate files are never
imported or executed by host-side materialization. Existing flat-file execution semantics remain.
The shared execution routine retains the existing gVisor restrictions and cleanup behavior.
Filesystem tests cover exact bytes, modes, private-parent enforcement and existing/symlink
rejection. A mandatory real sandbox case exercises nested module import and write denial.
This increment does not establish a complete approved repository-patch workflow or satisfy G1–G7.


### Contract-bound repository observations and accounting snapshot correction

The independent-output adapter now binds inventory bytes, the installed checker source manifest,
complete contract and resulting tree before isolated execution. Its tri-state result preserves
unknown execution/report outcomes. A licensed invoice fixture provides two modules, an exact
reconstructible Git base and independent cases; six real-sandbox variants are mandatory in the
infrastructure gate. This component still has no v2 work admission, quorum, registration or reuse.

Run 36211761633 passed the new nested-tree case but failed the complete infrastructure gate:
the legacy demo cached accounting before its final node barrier and reported 780 versus 779.
The reporting adapter now waits past the maximum worker-node height and reads a new state.
Regression tests reject a stale post-barrier response. The exact accounting assertion and frozen
v1 consensus semantics are unchanged. Subsequent real infrastructure evidence is required.


### Governed work-budget extension

The v2 control state now persists a fixed-budget ledger with target-bound phase reservations,
a protected verification allocation and conservative full-ceiling charges for unknown outcomes.
Configuration, reservation and settlement require the current administrative quorum. Ordinary
reservation saturation does not consume settlement/control journal reserves. Terminal tickets
are immutable and retained through request rollover. Signed SQLite replay and an independent
accounting reference model exercise the component. The 128-ticket active/history bound is
explicit; sustainable ticket archival and task/lease/residual integration remain incomplete.

The predecessor source 7abe55c passed run 36212387543, including the real repository sandbox
cases and final-accounting barrier correction. That result does not qualify this later extension.


### Isolated task ownership and deterministic expiry

Approved funding now attaches to one bounded task with an explicit worker allowlist, purpose,
lease window, final deadline and finite attempt limit. Signed lease/start/heartbeat/finish transitions
pin owner revision and fence. Pre-start expiry can retry within the limit; started expiry, key
revocation/retirement and cancellation preserve charged unknown work. Direct budget settlement
cannot detach or refund task-owned funding. Empty committed blocks apply expiry atomically.
Ten lifecycle/model/storage cases cover this component, including full signed replay; fence and
boundary-expiry mutations are detected. Source checks passed with 285 tests and 25 detected
mutations. This does not implement distributed worker dispatch or artifact adoption/reuse.

The predecessor budget source ab9aab6 passed hosted run 36212996745. That observation is scoped
to the budget increment, not the later task state-machine extension or the complete G1–G7 gates.


### Separate v2 consensus service and own-node transport

A separate CometBFT ABCI application now evaluates v2 raw signed transactions, retains transient
finalization, replays exact bytes at atomic commit and exposes only committed v2 queries.
Validator public keys are pinned separately from command credentials. The own-node client bounds
response bytes and preserves ambiguous submission outcomes. Fresh stores reject pre-funded/task
state, and snapshots reject future execution starts. A new mandatory installed-wheel laboratory
case joins consensus, scoped evidence storage and actual gVisor patch observation, then exercises
one-node loss, quorum loss and crash recovery. That case remains unqualified until CI is inspected.

Local static checks and distribution checks passed on Windows/Python 3.12. The full source suite
passed 298 cases with 25 detected mutations; a subsequent future-start regression also passed
in the focused 24-case suite. The wheel and sdist passed isolated installation and packaged-resource
checks. These observations do not qualify the new live ABCI workflow or the full operational profile.

The task-ownership predecessor 0b3210b passed run 36213818933. This does not qualify the later
ABCI adapter, supervised dispatch, artifact adoption, four-host operation or the complete G1–G7 gates.

### Funded organizational observations and conservative acceptance

The candidate extension requires four approved verification tasks under distinct organizations,
reserved funding and one exact target. Three passing organizations can establish scoped acceptance.
The fourth check and its funding remain active. Late failures, contradictory verdicts, withdrawals
and revoked historical evidence keys quarantine the candidate without deleting observations or
costs. Routine retirement retains evidence valid when signed; a rotated verifier can withdraw its
historical observation. Structural decoding checks all retained links and bounds.

Ten signed/model/storage/rotation cases cover the new component. The live installed-wheel case
now includes four actual sandbox checks, signed attestations, acceptance, subsequent withdrawal
and durable common-hash replay. This extension is not live-qualified until its exact-source CI
is inspected. Current artifact availability, dependency propagation, reusable status, sustainable
archival and a supervised worker remain incomplete.

The preceding ABCI source 4470200 passed run 36215652810, including its real four-node/gVisor case,
all six platform/Python jobs and managed signer. It does not qualify this later acceptance extension.

Audit also found that the inherited publication job depended on component tests but not the full
new G1–G7 gates. The development workflow now has a literal publication denial, including manual
tag dispatch. This temporary interlock is not an implementation or PASS result for those gates.
It must be replaced by verification of mandatory exact-artifact operational evidence before release;
an editable status ledger or operator toggle cannot authorize that replacement. Published 0.1.0
tag contents are unchanged.

Source 1c4b6a5660a3b005fc7d81dab2fb197ec3f980ee passed hosted run 36216530595: the six
platform/Python jobs, installed-wheel managed signer and all 16 mandatory single-host infrastructure
cases, including the extended four-verifier acceptance/withdrawal path. Local source checks passed
309 tests (three Linux-only skips, infrastructure/signer cases excluded separately), all 28 fault
injections and isolated wheel/sdist checks. The acceptance core has 100% line and branch coverage.
These are component results, not G1–G7 qualification or authorization to remove the release interlock.

### Exact-target repository reuse preparation

The repository adapter now reads scoped base/patch/inventory/observation objects on every preparation,
independently verifies their bytes, matches the complete intended contract and reconstructs the exact
result tree without executing code. Typed verdicts and report/attempt bindings must agree with retained
signed observations. Missing, corrupt or unavailable bytes reject despite old acceptance. The result
binds a committed snapshot and is not a future execution/effect permit.

The existing real laboratory case now prepares accepted bytes and rejects changed scope and withdrawn
evidence. A new mandatory case uses two content-distinct fixture bases with a reconstructible successor
Git commit, rejects inherited acceptance and funds four fresh actual sandbox checks for each target.
Its scope is bounded to two targets/eight invocations. Those later live changes remain unqualified until
their exact-source CI is inspected. Supervised freshness, replicated availability, lifecycle retention
and complete reuse accounting remain open.

Documentation predecessor 825a04d passed run 36216918034. This does not qualify the subsequent reuse
adapter or changed-base test.

Local source checks passed 335 cases, with three Linux-only skips and 18 infrastructure/signer
cases kept in their separate groups. All 30 invariant mutations were detected; the reuse adapter
has 100% line and branch coverage. Wheel/sdist isolated installation and packaged-resource checks
passed on Windows/Python 3.12. The successor fixture's Git commit ID and changed SHA-256 tree
bindings were independently exercised without executing candidate code. Live reuse qualification
is still required for the current source revision.

Source 6aceeac32622ff1cac83c4883c7cda0a8bbc9169 subsequently passed run 36217592717: all six
platform/Python jobs, real managed signer and all 17 required single-host infrastructure cases.
That includes current-object reuse preparation, withdrawal rejection and two content-distinct
bases with separately funded real gVisor verification. This qualifies those component mechanisms;
it does not supply S3, retention pins, supervised dispatch or the full operational release gates.

### Verified S3 byte-store increment

The [S3 adapter](s3-storage.md) uses explicit SigV4 credentials, validated TLS, conditional creation
and exact read-back verification. It does not repeat an ambiguous write. Scoped authority precedes
stream consumption and network requests. Corruption, absence, access denial and unknown publication
remain distinguishable. The optional `s3` dependency group keeps provider dependencies out of the
base package. The deterministic core has no new I/O dependencies.

Twenty-seven focused protocol cases passed, with 100% line/branch coverage. A real SeaweedFS 4.47
test passed in 30.29 seconds on Windows/Python 3.12.10 against an isolated installation of development
wheel SHA-256 `b174f96ef041bfa2202acd5b94551ba586034fda708a1a2fcfb830349dcdcef7`.
That test covers conditional concurrent publication, TLS and authorization denial, scoped keys,
empty/bounded objects, corruption, deletion, outage and restart persistence. Its record identifies
an uncommitted tree based on 311f4a6; it is not falsely attributed to that clean predecessor.

The exact same wheel also passed the real installed Vault check after extracting shared bounded
qualification-process handling. Both service harnesses reject skipped cases. New Windows/Linux
object-storage CI jobs consume the build job's wheel, independently verify pinned provider bytes
and retain component reports. Hosted results for this new increment must still be inspected.
Per-scope S3 capacity, availability quorum, retention, authenticated restore and worker supervision
remain unfinished. No G1–G7 status is promoted; the publication interlock remains active.

The preceding documentation revision 311f4a6996f1c1b8df7e5a21686bb6c3042a1146 passed
[run 36217957291](https://github.com/kadubon/checkedflow/actions/runs/36217957291).
That result predates this S3 increment and does not qualify it.

The combined local source check passed 362 cases in 80.15 seconds, with three Linux-only filesystem
skips and 19 infrastructure/service cases excluded into separate groups. All static checks and
32 selected invariant mutations passed. The isolated wheel/sdist package checks also passed.
The S3 and Vault service cases were executed separately as described above, not counted as source
passes. These results do not supply the remaining operational implementation or missing inventory.

The first S3 CI increment's Linux service job failed: it required every simultaneous conditional
write to be immediately confirmed, rather than preserving unknown replies and checking later
availability. The failure is retained in run 36219168437. A separate local restart check also found
that bucket-list readiness can precede volume registration, and that Linux graceful termination
can exceed a ten-second fixture wait. These were fixture assumptions, not grounds to weaken byte
integrity checks or add implicit write retries.

The revised fixture records every concurrent write outcome, performs only bounded read-based
reconciliation, waits for actual bytes during recovery, and deliberately kills its own process for
the outage/crash test. It adds interrupted-input nonpublication, interrupted-download rejection and
provider-credential replacement with old-credential denial. The 28 protocol cases passed. The
extended installed-service test passed on Windows/Python 3.12.10 in 31.90 seconds and, after the
explicit crash adjustment, Linux/WSL Python 3.12.3 in 42.09 seconds. Both used wheel SHA-256
`d595103221a856ed02ba52f567ad4ee433791e16136a16d2cedce04b15928ce4`; harness records identify dirty
source based on 4c4f9ff. The new exact-source hosted run remains necessary. No production runtime
behavior was relaxed, and neither local result claims power-loss durability or full G1–G7 coverage.

The revised local source suite passed 363 cases in 82.25 seconds, with the same three Linux-only
skips, 19 separately selected infrastructure/service cases, static checks and 32 killed mutations.
Run 36219168437 finished failed solely at its earlier Linux S3 job; all six platform jobs, managed
signer, Windows S3 and existing CometBFT/gVisor qualification succeeded. That failure remains a
failure, regardless of later local corrections.

The corrected source 88567f435092ff64cdef98dfecefd79c444d0be4 subsequently passed
[run 36219580713](https://github.com/kadubon/checkedflow/actions/runs/36219580713): all six
platform/Python jobs, installed-wheel CometBFT/gVisor, Vault and both Windows/Linux S3 service
jobs. Publication remained denied. This result predates the retention controller below.

### Durable retention controller

The [retention component](retention.md) adds private namespace/scope identity, transactional
capacity, persistent roots, grace intervals, read-only plans bound to a catalog revision,
tombstones committed before provider effects, explicit uncertain-erasure reconciliation and
rollback rejection below an independently supplied trusted floor. The local and S3 providers
have a separately authorized erasure primitive; ordinary worker access must not include it.
Provider-neutral access/byte contracts now live in `artifact_io.py`, with initial SDK imports
retained as re-exports and a static rule against provider dependencies in that interface.

Local source checks passed 387 cases in 91.44 seconds, with three Linux-only skips and 19
infrastructure/service cases selected separately. All static checks and 36 selected mutations
passed. Retention, common artifact I/O and both storage adapters reached 100% line/branch coverage.
The retention tests include a killed child process after tombstone commit and a separate bounded
Hypothesis state-machine oracle (12 examples, 35 operations each), using independent publication
and release timestamps rather than the catalog's stored deadline representation.

The final local development wheel, SHA-256
`5dda16fc2d1d9962b6a506e0768c90dadfcf4c9872032e182116323bfea30590`, passed isolated wheel/sdist
checks and actual S3/retention service checks on Windows/Python 3.12.10 and Linux/WSL Python 3.12.3.
The fixture protects a snapshot root, enforces grace, physically deletes through S3, restores the
old provider bytes, reopens the catalog and confirms persistent retirement. The harness explicitly
records an uncommitted tree based on 88567f4. Exact-source hosted qualification remains required.

These are component results. Complete lifecycle-root derivation, replicated availability admission,
authenticated catalog checkpoints/backups, governed maintenance integration, supervised work and
G1–G7 qualification remain unfinished. A caller-supplied stale recovery floor is not bootstrap trust.
No final 0.2.0 tag, release or PyPI publication has been performed.

### Portable retention-catalog backup

The retention revision d5109ec68583358f2653c5d51ec19f0d3ad12e88 passed
[run 36221287294](https://github.com/kadubon/checkedflow/actions/runs/36221287294): all six
Windows/Linux and Python 3.12–3.14 jobs, actual installed-wheel CometBFT/gVisor, Vault and both S3
service jobs. Publication was denied. That result predates the backup increment described here.

The [backup component](retention-backup.md) streams one consistent catalog snapshot into canonical
JSONL and restores it into a new private directory. It requires an independently authenticated
exact-byte checkpoint and a positive current revision floor, preserves tombstones, unknown
erasures, pins and accounting, and rejects invalid roots or counters. The active database appears
only after complete validation; existing stores are never overwritten. This is a concrete catalog
export/import path, not a complete consensus bootstrap or coordinated-service recovery claim.

Thirty-nine focused cases passed with 100% line and branch coverage. They include concurrent
source mutation during export, corruption and truncation, unauthorized access before I/O, stale
watermarks, scope mismatch, stream limits and a child process killed before activation. Two new
fault mutations remove the exact-digest and current-watermark guards; both were detected.

The complete Windows/Python 3.12.10 source check passed 426 cases in 100.53 seconds, with three
Linux-only filesystem skips and 19 separately selected infrastructure/service cases. Static checks
and all 38 selected mutations passed. Source and development distributions were scanned together:
596 files/members, no publication-scanner findings. This is automated inspection, not a guarantee
that every vulnerability is absent or an independent penetration test.

Development wheel SHA-256 `c00ca87268d7c0bccbd320d44c36a11ca1c5c8056db189ba4c2392ed8a5c4ff2`
and sdist SHA-256 `82bc4f7b729b1cf12b5c8949c015602256f59be9c24ce011bf644138b40ec452` passed
isolated installed-package checks, including portable catalog restoration outside the checkout.
The same wheel passed the actual SeaweedFS 4.47 fixture on Windows/Python 3.12.10 (36.73 seconds)
and Linux/WSL Python 3.12.3 (38.01 seconds). That fixture restores catalog tombstones after old
provider bytes reappear. Both records identify a dirty harness based on d5109ec; they do not
qualify subsequent source changes or the final operational release.

Remaining recovery work includes independent protected checkpoint custody, current lifecycle-root
derivation, coordinated provider/catalog/agent journals, exclusive controller replacement,
authenticated consensus bootstrap and measured cross-host restoration. Broader unfinished items
still include sustained state archival, residual propagation, supervised work, v2 agent-access
integration, external-effect recovery, deployment/observability and G1–G7 qualification. The
required approved four-host inventory and disposable GitHub effect target remain unavailable.
The publication interlock remains active; no 0.2.0 release is claimed.

### Exact-destination GitHub draft provider

Backup source 7a7f13fdbb556659ff8cbee8cdd39ef0f21b542c passed
[run 36227112812](https://github.com/kadubon/checkedflow/actions/runs/36227112812), including all
six Windows/Linux and Python-version jobs, actual installed-wheel CometBFT/gVisor, Vault and both
S3 jobs. Dependency scanning found no known vulnerabilities. This predates the provider below.

The owner subsequently authorized creation of a disposable GitHub target. A separate private
repository was created, Actions disabled, and its immutable numeric ID retained locally. The
earlier missing-target observation is resolved. Four-host inventory remains unavailable.

The [draft provider](github-drafts.md) supplies a concrete, disabled-by-default adapter for exact
pre-staged commits. Repository name and numeric identity are bound both in the plan and configured
destination. Preflight checks Actions policy, both branches, parent and Git tree. An SQLite claim
commits before sending; repeated requests cannot automatically POST again. Read reconciliation
keeps absence uncertain and refuses foreign, modified, closed or non-draft objects. Confirmation
remains a historical observation; a different object cannot replace the original receipt.

Thirty-eight provider tests passed at 100% line/branch coverage. They include protocol fixtures,
a real loopback HTTP server dropping a successful reply, concurrent claim acquisition, malformed
responses, changed policy/content, Boolean identity rejection and an actual process exit before
send. The full source check passed 464 cases in 94.82 seconds, with three Linux-only filesystem
skips and 19 infrastructure/service cases selected separately. All static checks and 40 selected
invariant mutations passed. A source/distribution publication scan checked 611 members with no
findings. These are automated component checks, not independent penetration-test results.

The development wheel `f999ae3bd82cbf1e155b0eda59225eee5864778d2d74b00e91df867ba1c354d0`
and sdist `a63a6dbbb269de5d9ba5220497f2e01197a84b55ca1653c1bb51f917c62bb236` passed isolated
installation tests. The same wheel was installed separately outside the checkout; its package
files were compared byte-for-byte with the archive before an actual GitHub provider smoke.
That smoke created one real draft, retained the same receipt on duplicate dispatch, reconciled
the original draft, closed it through explicit operator cleanup, and removed its unchanged owned
branch. The four bounded development fixture runs, including earlier revisions, were all cleaned
up; none merged or executed candidate code. Their private journals and provider object identities
remain local. The final recorded smoke used Windows/Python 3.12.10 and API version 2026-03-10,
with uncommitted source based on 7a7f13f. Exact-source hosted CI remains a separate check.

The fixture stages inert text and uses the operator's existing `gh` credential. It does not prove
least-privilege runtime custody, consensus authorization, accepted-patch-to-Git staging, governed
compensation or complete external-effect recovery. Those integrations remain required before G3
or the complete operational profile can pass. The new `effects` extra adds HTTPX without changing
the base SDK dependency boundary. No runtime-driven production write, 0.2.0 publication, paid
provisioning or companion-project modification has occurred.

### Dispatch freshness increment

The preceding provider source `4012ee800f5a2511ab23b8b0b8cac68db2d57cb7` passed all component jobs
in [run 36229225801](https://github.com/kadubon/checkedflow/actions/runs/36229225801): six
Windows/Linux Python jobs, 17 real CometBFT/gVisor cases, real Vault and both real S3 jobs.
The dependency audit reported no known vulnerabilities; the publication scan found no issues in
611 members. Publication was skipped. This result predates the watchdog and does not qualify it.

The [dispatch watchdog](dispatch-watchdog.md) adds process-local inhibition based on bounded
read age and committed-height progress. Its own-node reader checks the pinned chain and sync flag
before reading v2 state. Repeated successful reads of a stopped height cannot extend readiness.
Slow/failed reads and maintenance break the warmup sequence; rollback, same-height divergence,
clock regression and emergency stop prevent further dispatch through the instance. A network
read does not hold the stop lock. No clock enters the pure state machine and no observation grants
task or effect authority. Durable supervision and dispatch integration remain incomplete.

The Windows/Python 3.12.10 source suite passed 496 cases in 95.92 seconds, with three Linux-only
filesystem skips and 19 infrastructure/service cases selected separately. The 21 new watchdog
cases reached 100% statement/branch coverage; eleven new client cases cover sync/chain/height
admission. Initial test expectations for invalid integers were corrected to the existing `SHAPE`
code. Bandit initially rejected a redundant type-narrowing assertion; an explicit runtime failure
branch replaced it without suppressing the rule. The initial failure log remains local.
The corrected full run passed all static checks and detected all 42 selected invariant mutations,
including unchanged-height renewal and omitted read-age expiry.

Fresh isolated installations of the development wheel
`3fe6c2383d213869b34cb0551a36cdf4107e8e60f3bb217b01ec980f069923a5` and sdist
`89866d91cecb2a4ce7b6af4aa618a63bc2a929d0497b4603e6ccf1273657e029` passed outside the checkout.
The source/distribution scan examined 618 members with zero findings. These artifacts contain
uncommitted source based on 4012ee8, preceding this evidence update; they retain development
0.1.0 metadata and cannot replace the published baseline. The extended real-node test requires
its own exact-source hosted run, separately from the installed cold-start smoke.

### Owner-authorized Linux VM experiments

The final preceding source `f695b870311e44733aabb90c6d2f64d98a1b8365` passed all component jobs
in [run 36230700551](https://github.com/kadubon/checkedflow/actions/runs/36230700551); publication
was skipped. Four disposable QEMU/KVM environments were then created under the owner's new
authorization, with separate kernels and disks on one physical/WSL host and one administrator.
The same CI wheel was installed in all four guests and an isolated controller environment.

Actual gVisor preflights passed on all four. Two successful cross-kernel experiments covered
agreement, competing acquisition, one-node interruption, two-node quorum loss, watchdog
inhibition and recovery. One used process crashes; the other disabled guest peer interfaces
while services remained running. All four states and budgets agreed after each recovery.
The first process-fault attempt failed in the operator helper and is retained as failed.
Details, artifact identities, corrections and limitations are in the
[dated VM report](vm-laboratory-2026-09-26.md). All guests and the virtual network helper were
stopped cleanly, preserving private disks and evidence for a future bounded session.

G6 is now `IN_PROGRESS`, not blocked by missing host permission or inventory. It has not passed:
the maintained deployment harness, delayed replies, storage/key-service outages, replacement,
coordinated restoration, upgrades and complete declared workload remain required. Private operator
fixtures do not supply the deployment product or protected release evidence. Other incomplete
implementation milestones remain unchanged. No 0.2.0 publication occurred.

### Application history backup and staged replay

The preceding source `fb0f0134548e7bdeba28c2af25b110ed42dab446` passed every component job in
[run 36233055924](https://github.com/kadubon/checkedflow/actions/runs/36233055924). Publication was
skipped; that run predates this recovery component.

The [application recovery adapter](application-backup.md) streams a verified consistent WAL read
snapshot and restores original signed bytes into a new private application database. A separately
trusted genesis, checkpoint and current height floor are mandatory. Replay reconstructs archives
and retains rejections, unknown charges, nonce/key history, tasks and evidence state. Full content
checks precede activation. Failed staging is preserved; existing destinations are never replaced.
The format has a packaged JSON Schema and canonical public conformance vector.

Thirty-five dedicated source cases passed at 100% statement/branch coverage. They include a writer
committing during export, real process exit immediately before/after activation, corrupted histories,
rollback floors, uncertain costs and persistence errors. An initial Windows run found that syncing
a read-only file descriptor failed; the owned pending database is now opened for read/write sync.
The corrected full Windows/Python 3.12.10 check passed 531 cases in 115.36 seconds, with three
Linux-only filesystem skips and 19 infrastructure/service cases selected separately. All static
checks and all 44 selected invariant mutations passed, including omitted restore-floor and replay
checks. A source/distribution publication scan checked 633 members with no findings.

The development wheel `41b2cb412edb9941df36ed1a8f9e4c60ea98d716b20f4b0f37d7116c39ede60d`
and sdist `0e972784411f42411638291e53722766b5c869eeec856a8b3f23682206b7ba42` passed isolated
installed-package checks. They contain uncommitted source based on fb0f013, before this evidence
entry, and retain unpublishable 0.1.0 development metadata. They cannot replace public 0.1.0.

The owner-approved four-VM lab was resumed for a new bounded session using its existing disks.
The exact same wheel was installed in all four guests; each of its 129 package members was compared
with the archive. Each guest exported and restored its retained 36-block history from the earlier
real communication-fault experiment. All restored states, task ownership and budgets matched the
originals. The controller retained checkpoints before restoration; this is local operator custody,
not protected consensus checkpoint distribution. No validator was started or signing state copied.
All guests and the network helper were subsequently stopped cleanly, retaining private evidence.

A subsequent review found that a locally reformatted unsigned block container could verify yet
produce a noncanonical export rejected by restoration. Export now canonicalizes that container,
preserving the original transaction bytes. A new regression case raises the dedicated suite to
36 passing cases with 100% statement/branch coverage. The VM and development-distribution
observations above precede this final normalization change; they are not reclassified as tests
of a different artifact. The complete final source and hosted artifact checks remain separate.

The base CLI now provides `application-backup export`, `inspect-checkpoint` and `restore`, with
a packaged machine-readable command catalogue. Six CLI tests cover normal round-trip operation,
metadata-only inspection, overwrite refusal, missing/corrupt storage, bounded inputs and failed
exports without a completion checkpoint. SQLite errors return the machine-readable `STORAGE`
code. Together with the 36 SDK cases, these checks cover the application-only operator path;
they do not provide service drain/start, full node recovery or a protected release workflow.

The extended installed-wheel CometBFT/gVisor CI case now restores each stopped node after actual
artifact checking and withdrawal. It requires a new exact-source run; the earlier run cannot qualify
it. Application-only recovery does not complete node bootstrap, signing-state ownership, callback
or provider journals, coordinated service recovery, upgrades, protected checkpoint custody, G6 or
the final release gates. Those implementation obligations remain open; publication stays disabled.


## Settled-work retirement increment

The draft v2 `history.archive` command reclaims selected known terminal funding/task/candidate
records from retired request epochs during paused maintenance, under current three-organization
administrative approval. Cumulative spending and verification allocations survive removal; retained
tasks pin funding and retained candidates pin checks. Unknown obligations cannot be archived.
Atomic SQLite persistence, independently anchored retrieval, signed replay and application-backup
restoration preserve the complete retired batch. Existing pre-retirement v2 state hashes remain
unchanged; a packaged signed conformance vector fixes the new behavior. The database extension
requires coordinated upgraded binaries, not an implemented rolling-upgrade mechanism.

The initial targeted source run passed 24 retirement cases, including a pure accounting sequence
of 4,608 records in 36 bounded batches, signed authority checks, dependency pins, storage corruption,
transaction rollback and backup replay. The 4,608-record case is a deterministic accounting test,
not a deployed long-running worker experiment. A packaged signed-vector case was subsequently
added. The first whole-source run passed 555 cases with three Windows platform skips and met
all coverage floors, but correctly failed because an existing fault-injection target no longer
matched the changed spending expression. The target was updated without weakening the expected
invariant. Later final checks and hosted infrastructure evidence must be recorded separately.

The real CometBFT/gVisor test now includes governed retirement after four funded checks and an
adverse withdrawal, requires all four nodes to agree on cumulative costs, and restores each archive
from application history. Merely adding this test does not establish an executed result. Credential
retirement, full dependency/effect graphs, supervised indefinite operation and G1-G7 remain open.


The final local source check passed 563 cases on Windows/Python 3.12.10, with three Linux-only
skips and 19 separately selected infrastructure/service cases. All 47 selected invariant mutants
were detected. The new retirement core, codec and storage boundaries reached 100% statement and
branch coverage. Ruff, strict mypy, Bandit, offline contracts and actionlint passed. The isolated
wheel and sdist installation checks, including signed retirement-vector replay and optional agent
SDK smoke checks, passed outside the checkout. The source/distribution scan examined 657 members
with zero findings. These local results do not qualify the new real-infrastructure case or release.


## Durable worker submission and supervision increment

The worker SDK now serializes all commands for a signing identity, persists original signed bytes
before submission and confirms them through own-node receipts. Ambiguous sends block replacement
requests; explicit retransmission is identical-byte and bounded to three transport calls. A complete
immediately preceding request archive anchored in current state can establish commitment or retired
nonmembership. Empty live queries cannot establish absence. Local journals preserve observation
floors and exclude concurrent local callers without pretending to elect a cross-host leader.

The concurrency-one supervisor connects committed lease/start, a durable pre-invocation record,
current freshness/fence checks, one bounded trusted observer, persisted results, scoped verified
evidence publication and signed completion. Restart never reruns already-started code. Missing
results become unknown; saved results can be republished without execution. Optional heartbeats
share the same nonce coordinator. The concrete repository adapter invokes only the existing
fail-closed gVisor observer and binds its exact funded contract. A report is not verifier acceptance.

The initial targeted source run passed 37 cases with 100% statement/branch coverage for all three
new modules. It included actual child-process exits during execution and before publication.
Additional process-exit cases for commands before/after remote commitment and an exclusion test
were then added. A whole-source check initially flagged two redundant type-narrowing assertions;
these were replaced by casts after the existing mandatory checks, without suppressing Bandit.
Final source and exact-artifact infrastructure results are recorded separately.

The installed-wheel CometBFT/gVisor case now drives the actual patch through the new supervisor,
interrupts one node before observation, sends a heartbeat through the same coordinator, publishes
verified evidence and reopens the supervisor without invoking the observer again. Merely adding
that test does not establish a passing execution. Unattended scheduling, bounded generation/reuse
loops, full dependency/effect graphs, coordinated worker-journal restore, protected bootstrap,
versioned migration, deployment/observability and complete G1-G7 qualification remain required.


A runner audit identified an additional release-critical gap: a hard kill of the supervising process
can bypass its container cleanup and wall-clock enforcement. Durable worker journals inhibit duplicate
execution but do not terminate an orphan. A separately supervised, ownership-checked orphan recovery
path and actual live-container crash qualification remain required. The benign process-exit fixtures
must not be cited as proof of sandbox termination after supervisor death.


Final local validation for this increment passed 603 source cases on Windows/Python 3.12.10,
with three Linux-only descriptor skips and 19 infrastructure/service cases separately selected.
All 50 selected invariant mutants were detected. All three new worker boundaries reached 100%
statement/branch coverage. Static checks, isolated wheel/sdist installations and agent SDK smoke
checks passed; the source/distribution publication scan examined 679 members with zero findings.
These results do not qualify orphan-container cleanup or replace exact-source real infrastructure
execution. The publication interlock remains closed.


A follow-up continuity audit found that chain/mission/actor names alone could allow local execution
records to cross deployments if names were reused. The coordinator now pins the immutable
application-origin hash reconstructed from the validating node's initial ownership and limits;
the execution journal binds the same hash. Four regression cases reject changed initial ownership
and old local journal layouts while preserving routine key rotation. The combined targeted suite
now passes 44 cases. This check preserves continuity after a trusted first observation; it does not
turn discovery or a self-supplied genesis into bootstrap authority. This source change requires
fresh full and exact-artifact qualification rather than inheriting the earlier run.


The origin-binding follow-up passed the full local check: 607 source cases, three Windows-only
platform skips, all 51 invariant mutants detected, and all required coverage floors satisfied.
Both development distributions passed isolated installation before the final documentation-link
clarifications; final hosted artifacts must be built and tested again. The earlier source
`1f6560627adb90f7bc9ac0ea8f12694c34eea650` completed CI run 36237204531 successfully, including
17 actual CometBFT/gVisor cases in 260.979 seconds and all six platform/Python jobs. That run
qualifies the earlier supervisor wiring only, not the subsequent origin-binding change or orphan
recovery. No full release gate is promoted by either component result.

## Cached admission and independent sandbox recovery increment

The origin-binding source `c403cd2e629e9050f2c4484faff81e8dd307c9cb` completed component CI
36237724862 successfully. A subsequent review found that cached command acknowledgment did not
reapply current authentication. Repeating a confirmed request now evaluates its original signed
bytes under current admission rules. New regression cases reject retired request epochs and revoked
worker keys without sending again. The interim local check passed 609 cases, three platform skips
and all 52 selected mutants before the following sandbox changes were added.

The draft runner now journals container creation intent before calling Docker create, verifies and
persists its immutable ID before start, and requires a fresh independent recovery service. The
service keeps uncertain creation outcomes, verifies ownership before removal, recognizes prior-boot
deadlines, and pins learned IDs before destructive cleanup. A managed-container inventory prevents
an empty replacement journal from advertising readiness over existing work. The journal has 32
unresolved slots; capacity exhaustion inhibits execution. Docker access is explicitly local, restart
policies and image health checks are disabled, and persistent container logging is disabled.

The source suite adds ownership, absence, lost reply, concurrency, deadline, daemon continuity and
create-before-start tests. The installed-wheel qualification now requires two additional actual
failure windows: worker exit after creating a stopped container, and SIGKILL after observing a
running gVisor container. Only successful execution of that exact artifact qualifies these cases;
the older 17-case infrastructure runs cannot substitute for them. Full source checks, package checks
and fresh hosted qualification are pending for this source increment.

The [recovery runbook](sandbox-recovery.md) explains the new required configuration and failure
semantics. This is not a complete operational deployment: service-manager restart qualification,
orphan temporary-workspace collection, journal disaster recovery, unattended scheduling and the
remaining G1–G7 requirements are still open. The literal publication interlock remains closed.

Source `9521aa7deb2b73b86416e556cab0ec404341e0bb` completed CI 36239137463 successfully on all
six OS/Python jobs, the build, managed signer and both object-storage jobs. The installed-wheel
CometBFT/gVisor suite executed 19 cases with no skips/errors/failures in 334.991 seconds, including
the created-container crash window (13.532 seconds) and running-container crash window (8.824 seconds).
Its wheel SHA-256 is `73025ba6462f5688cc671449f3f55ee037ec17337702449460189bbd791576a4`;
sdist SHA-256 is `addcf16d60eb4cec2becc428a15454ffaee9d0331b4bfac6999272b7d4397104`.
Local source validation reached 634 passes, three Windows descriptor skips, and 55 detected mutants.
These development distributions retain 0.1.0 metadata and must not replace public 0.1.0 files.

The next qualification increment adds an actual transient-systemd recovery restart experiment.
It uses the same wheel import boundary, injects worker and recovery-service SIGKILL, and requires
automatic service restart plus container/journal retirement. This new required case is pending
exact-source execution; the preceding 19-case result does not qualify it. No release gate is
promoted on the strength of the added test or the service template.

CI 36277633882 passed for `ee0f3dfa362c0959a96d23f20fe89ecdc6c08ec1`: all component jobs,
six OS/Python jobs and 20 installed-wheel infrastructure cases passed, with no infrastructure skips,
errors or failures (301.563 seconds). On systemd 255 (255.4-1ubuntu8.17), the transient recovery
service restarted once after SIGKILL; orphan execution and its journal entry were retired in an
observed total 15.605 seconds from worker launch for a 15-second container deadline. That measured
total includes provisioning/observation latency; it is not a hard real-time or production SLO claim.
The exact wheel remained `73025ba6462f5688cc671449f3f55ee037ec17337702449460189bbd791576a4`;
the sdist is `316d1830b54bc4217460b6b940b0c9f272de29868463892e1d15a66eb5e9d750`.

## Bounded local execution records

A longevity review found that consensus history could be retired while worker recovery buffers
continued growing. The worker now checks a 128-record local ceiling before leasing or starting new
work. Existing records can still reconcile. During a committed pause, explicit `Supervisor.retire`
republishes exact known-finished evidence, rechecks the completion and pause, then removes selected
buffers atomically. It must precede consensus history archival. Unknown results, absent active proof,
pending commands, changed completion and publication errors retain the local data. No budget is
refunded, artifact accepted, or execution retried by this operation.

Seventeen targeted retirement cases pass, including real process exits during evidence retention
and after local commit. Worker supervisor statement/branch coverage remains 100% in the combined
targeted suite before these two extra process-exit cases. The actual v2 infrastructure case now also
checks buffer retirement and exact evidence retrieval before consensus archival. Fresh full and
same-artifact qualification is pending for this new source; prior infrastructure results do not
qualify the added maintenance path. Logical record bounds do not replace disk quotas, archive-based
worker recovery, artifact replicas or coordinated disaster recovery.

The bounded-buffer source `5ffe561afe94499452efe5cdd9545e482d4d05ae` passed CI 36278781917.
All six OS/Python jobs, Vault and both object-store jobs passed. Its 20 installed-wheel infrastructure
cases had zero failures/errors/skips (320.925 seconds). Wheel SHA-256 is
`fc495e291fb02f5088efd718f42e6bbac3323718243a7392f5a215cd574f0532`; sdist is
`d746b0f87cc4488e70e305f094a502c971f471394f530635bdc7379926c22c9d`. Local validation passed
651 tests and 57 selected mutants. The source/distribution publication scan inspected 695 members
without findings. This development metadata remains 0.1.0 and must not be published over 0.1.0.

## Finite persistent execution scheduling

The next increment implements an immutable task schedule outside consensus. Local call allowances,
boot-bound deadlines, retry due times and emergency stop survive process restart. Only explicit
transient classes are retried with bounded exponential waiting and jitter. Limits are charged before
invocation, and the existing supervisor prevents uncertain code from being executed again.
Thirty targeted cases passed, including an actual process exit and recovery through the worker's
persisted attempt. The installed-wheel infrastructure case now runs its patch task through the
scheduler. Exact-source full/installed qualification for this increment is pending.

README, the Docs index and worker guides, AGENTS.md, skills.md and the machine-readable operation
catalogue describe the new behavior and limits. This does not complete automatic generation,
verification/reuse orchestration, cross-host deployment, boot/validator recovery, consensus-bound
effects or uniform v2 A2A/MCP authorization. The remaining G1-G7 gates and final release stay open.

The finite scheduler source `f486af29d728a84d6377c95e23046033dacb1143` completed CI 36279970168
successfully, including six OS/Python jobs, Vault, both object-store jobs and all 20 installed-wheel
infrastructure cases (318.797 seconds, zero skips/errors/failures). Local source checks passed
681 cases, three Windows-only platform skips, and all 59 selected mutants. The scheduler had
150/150 statements and 26/26 branches covered, with no exclusions. Wheel SHA-256 is
`167df86bbbde324404184995f83f555f51bd4adfc892aff902991341fd1b7345`; sdist is
`e0dd08b8888ed22f9f3822418935431338a9e28d30127ce7ec96cb6cbf66cb3e`. The 702-member
source/distribution publication scan found no pattern matches. This remains development 0.1.0
metadata and does not authorize final 0.2.0 publication.

## Native v2 agent gateway

A common transport-facing gateway interface now supplies mission snapshots, command parsing,
protocol/schema discovery and receipt admission. A2A history and MCP notifications/completion no
longer depend on v1 consensus state classes. The new v2 gateway accepts original signed bytes,
checks current authentication/admission, sends once and requires the committed digest. The CLI
selects it explicitly with `--protocol v2`; v1 remains the default.

Native task status, evidence digests, modeled accounting and current candidate acceptance are
projected without fabricating residuals or cancellation authority. Eighty-eight targeted v1/v2
agent cases passed, including the official MCP client and A2A handler lifecycle; the v2 gateway
has 100% statement/branch coverage in that suite. The actual installed-wheel v2 case now sends
worker commands through MCP to CometBFT and observes the A2A receipt projection. This changed
path requires a new exact-source infrastructure run; the scheduler CI above does not qualify it.

Docs, README, AGENTS.md and skills.md describe both the API and its unfinished boundaries.
Equivalent per-client authorization, callback-secret custody, archived-record policy, mTLS,
coordinated gateway recovery and full G1-G7 remain incomplete. No release gate is promoted.


The native v2 gateway source `e160b76978f4c6e6e1f041b292fa24a75df26e93` passed
[CI 36280787064](https://github.com/kadubon/checkedflow/actions/runs/36280787064), including all
20 installed-wheel infrastructure cases (291.859 seconds, no skips/errors/failures), six platform
jobs, Vault and both object-store jobs. Local checks passed 697 tests, three platform skips and
61 selected mutants. Wheel SHA-256 is `be99bb39e75f3140b9dcb94b3b4169d3db4d0f068ddfd39005462dce40d5a230`;
sdist is `60739c3757bfa50309d00c5b8805a16f2f0a001a468e66fb527640406bc89284`.
The 709-member publication-pattern scan found no matches. These artifacts retain development
0.1.0 metadata and cannot overwrite the public release.

## Callback secret custody

Persistent A2A callback configurations now require an operator-supplied bounded keyring. AES-256-GCM
binds each encrypted row to its journal, mission, task and configuration identity. Configuration
responses omit tokens and authentication credentials. Delivery uses the decrypted original internally;
no plaintext fallback or automatically generated persistent key is allowed. Explicit rotation changes
all rows atomically and preserves delivery counters. Legacy plaintext rows require a deliberate
migration decision; old backups and SQLite remnants are not silently erased.

The A2A authentication contract uses singular `scheme`, with case-insensitive Bearer support.
Targeted tests include actual protobuf response redaction, row substitution, incorrect keys,
ciphertext tampering, restart, failed rotation and actual process exit during rotation. The combined
agent suite passed 108 cases with 100% statement/branch coverage for the secret and journal modules.
Full source and installed-artifact checks for this increment remain pending. See
[callback storage and recovery](callback-secrets.md) for operator steps and the packaged schema.
This partially addresses R8-0406; per-client ownership, equivalent endpoint authorization,
coordinated key recovery and the full release gates remain open.


Local callback source validation passed 717 tests (142.94 seconds), with three Windows-only
filesystem skips and 22 infrastructure cases separated from this suite. An additional packaged
keyring-schema boundary case was then added and all 21 callback cases passed. Static checks,
coverage thresholds and the publication-pattern scan passed. Mutation and exact installed-wheel
results are recorded separately when complete; these component results do not close G1-G7.


A follow-up audit found that callback page values and the ciphertext revision were read separately.
They now come from one locked SQLite snapshot. A deterministic interleaving regression replaces a
configuration during page projection and verifies that the cursor describes the returned values,
then rejects continuation against the changed state. This closes a pagination race introduced by
the ciphertext-revision change; its source and installed-artifact checks require the follow-up commit.


The callback snapshot source `551cccab8f82e1f386f7b499056c61bf13ebea53` passed
[CI 36282190089](https://github.com/kadubon/checkedflow/actions/runs/36282190089), including all
20 installed-wheel infrastructure cases, all six OS/Python jobs, Vault and both object stores.
Wheel SHA-256 is `e8e9a1edd4e2173bec3862813f59a6d710cd31e138a4553694fe46145a2d1dc4`;
sdist is `e58536669ec310bb828607e2d5aefd64d5734a9ef324b3081a1c59d7d86f020e`.
Its source suite passed 719 cases, three platform skips and all 64 selected mutants were detected.
These results do not qualify subsequent client-policy changes.

The preceding CI 36281959531 failed one sandbox recovery fixture: asynchronous container deletion
raced Docker's list-then-inspect read. The subsequent policy increment corrects that fixture to
retain an uncertain observation and reread within its existing deadline, still requiring both
confirmed absence and journal retirement. Persistent unknown results still fail qualification.
No production cleanup failure is reinterpreted as success.

## Mission and client access

A shared protected-file policy now binds verified issuer/client/subject, chain/mission, explicit
roles and signed actor IDs. The v2 CLI requires it. A2A JSON-RPC/HTTP+JSON/gRPC and MCP operations
use the same decisions, including egress rechecks, callback ownership and client-bound cursors.
Administrative submission is explicit and still needs current three-organization signatures.
The ordinary direct SDK gateway retains its worker/verifier-only default. OAuth identity includes
the verified issuer; tokens cannot assert roles through message metadata.

The [client access guide](client-access.md), packaged schema/role mapping/decision vectors, README,
AGENTS.md and skills.md explain configuration and limits. Tests use actual loopback services and
protocol clients, including denied cross-client callbacks, role/actor changes, stream revocation,
subscription filtering and dispatch rechecks after DNS. The installed v2 CometBFT worker case now
uses the policy-enabled MCP server. Full exact-source qualification for this increment is pending.
R8-0406 remains in progress: unified artifact-download/archive authorization, mTLS deployment and
coordinated recovery are not completed by transport policy. No G1-G7 release gate is promoted.


A follow-up to client-policy admission handles signed journal rollover receipts: they move into the
new archive rather than remaining in the active journal. The gateway now confirms only an exact
committed root matching the independently predicted batch. A concurrent change to the batch leaves
`OUTCOME_UNKNOWN`, and an old-epoch duplicate is still rejected. Forty-one targeted gateway/access
cases passed, with 100% statement/branch gateway coverage; full follow-up checks are pending.


The rollover follow-up `a355374023e0838d4cd5b86ce26b46bf32d89a2b` passed 744 source tests
(three Windows platform skips and 22 separately qualified infrastructure cases), all coverage gates,
and all 68 selected fault mutants. These source results precede the download increment below.

## Explicit artifact publication

An optional protected catalog binds complete references to a chain and mission before bytes can be
served. A2A and MCP HTTP applications share the authenticated download boundary; MCP also exposes
an optional read tool using the same Reader. Policy, reference publication and provider integrity
are checked around storage reads. Catalogs are bounded and fail closed. The CLI requires both a
catalog and an existing local database under v2 client policy. Archived bytes use the same rules;
no automatic publication, trusted replay root or indexed historical query is inferred.

The first targeted suite passed 117 protocol/access/download cases. Download statement and branch
coverage were both 100%. Full-source, installed-package and mutation checks for the final increment
are recorded separately; these component results do not qualify a release. R8-0406 remains in
progress pending broader operational integration and original G1-G7 evidence.


The pre-download source `a355374023e0838d4cd5b86ce26b46bf32d89a2b` also passed
[CI 36283956247](https://github.com/kadubon/checkedflow/actions/runs/36283956247): six OS/Python
jobs, managed Vault signing, both real object-store jobs and all 20 installed-wheel infrastructure
cases. The downloaded infrastructure report independently passed the required-case gate with no
skips, failures or errors. Wheel SHA-256 is
`e9897b956825daf1a607d5f69f61399fd11671e4a95ee423f480fa6e125725eb`; sdist is
`cde1ea9966ca006f393f76c365241940ebd746ec65b8e42ed94f633e585a3865`.
These development artifacts retain 0.1.0 metadata and must not overwrite the published 0.1.0.
They do not qualify subsequent publication-catalog changes.


Artifact-publication source `15aa65ef41a2ec91c145ed325c3a13cc44ff6f97` passed
[CI 36284800536](https://github.com/kadubon/checkedflow/actions/runs/36284800536), including all
six OS/Python jobs, managed signing, both object stores and 20 installed-wheel infrastructure cases.
Its local source suite passed 767 tests, three platform skips and all 70 selected mutants. Download
coverage was 100%; 748 source/distribution members had no publication-pattern findings. Wheel
SHA-256 is `174b1edcb44fcf5a7c30ed5f38682e320025eb2032a15582aa87776d31f70808`; sdist is
`db0928e0f01309999635423b430c136dbf37fe2bbc94521568ade6a5711b3ae3`. These results precede TLS changes.

## Mutual TLS transport protection

The CLI now accepts an operator-provisioned server certificate/key and client CA together. HTTP
and A2A gRPC require valid client certificates when configured; their startup snapshot is separate
from Bearer identity and mission policy. Forwarded-header rewriting is explicitly disabled. Secure
advertised proxy URLs are operator configuration, never request metadata. No listener is exposed
beyond the existing loopback defaults. The proxy recipe requires separate deployment qualification.

Twenty-four dedicated source cases passed, including real HTTP/gRPC handshakes, missing/foreign/
expired certificates, wrong server trust, retained Bearer checks, current grant withdrawal, CA
replacement, startup races, CLI rejection and proxy-header spoofing. The TLS boundary has 100%
statement coverage (no measured branch arcs). The initial handshake fixture failed because CA and
leaf subjects were identical; distinct subjects and standards-compliant key-usage/identifier
extensions corrected the fixture. No production TLS verification was weakened.

Five named real transport cases extend installed-wheel infrastructure qualification to 25 required
cases; old 20-case reports cannot qualify this increment. Exact installed results and full-source/
mutation results remain pending. No G1-G7 gate is promoted; coordinated credential retirement,
proxy/multi-host deployment and original operational requirements remain incomplete.


The initial TLS source `c587e8fd63f08cc9990ec0b8f8c35aeda0f1745a` passed 794 source tests
(three platform skips, 22 separate infrastructure cases), all coverage gates and all 73 selected
fault mutants. A discovery audit then found its Agent Card omitted the standard mTLS security
scheme. The follow-up declares mTLS and Bearer together as one AND requirement for CLI TLS listeners,
with an explicit SDK declaration flag and actual HTTPS discovery regression. Plain local cards keep
their prior Bearer-only declaration. This follow-up needs its own source/installed checks.

### Complete-source Git binding for draft effects

TLS discovery source `2ec99174aa7108059f8bffe932925c5bf37f6315` passed 795 source tests,
all coverage gates and all 73 fault mutants. Its [CI run 36286140113](https://github.com/kadubon/checkedflow/actions/runs/36286140113)
passed every component job. The downloaded installed-wheel report was independently checked:
25 required infrastructure cases passed with zero skipped, failed or errored cases. Its wheel is
`b79aeaf9286f0cbca6264b39395ba41f50ddc69287dae92b1d1b659e20932c95`; its sdist is
`858117c4e7ec78968c14dea1c1b516ba94c0869820958008e30aba6b79ce3675`.
These results precede the Git binding increment below and are not full G1-G7 qualification.

The new `dispatch_patch` and `reconcile_patch` methods validate the complete immutable source,
patch permission and contract bindings, reconstruct Git object identities, and compare the base
commit tree before invoking the existing provider. The staged head must still have the exact
result tree and single approved parent. Retained receipts cannot bypass source/patch checks.
The lower-level provider API remains metadata-only. Current consensus authority, staging writes,
exclusive executor ownership, recovery and compensation still require operational integration.

Sixteen focused source cases cover the byte bindings and actual Git plumbing comparisons.
Portable vectors include directory ordering, empty files, nested paths, Unicode and CRLF bytes.
The installed smoke initially exposed a Windows locale-dependent vector read; explicit UTF-8
fixes the test harness. An initial full run passed 810 tests, but coverage was invalidated by a
concurrent source documentation edit; that run is not a passing full gate. A fresh full run with
fixed source and isolated wheel/sdist installation is required and recorded separately.

The Git-binding development wheel
`70e3bffddd7bd7922c37bfbf06d8d0db44c68e90a2002ebda67d9aece0a03d8d` and sdist
`77b2ed33d7f9b8250749fc3f60941fade42fe254ca71de0e26a03dae6d8ee90e` passed isolated
installation after the explicit UTF-8 harness correction. The same wheel was installed outside
the checkout, compared byte-for-byte, and used for an authorized real disposable GitHub smoke.
Complete README source and a comment-only Python patch matched the actual Git trees. Creation,
repeat receipt lookup, read reconciliation and operator cleanup passed. The owned draft was
closed without merging, its unchanged owned branch removed, and Actions remained disabled.
No candidate code was executed and no checker acceptance or consensus authority was claimed.
Current package files equal that tested wheel. The source check passed 811 tests in 178.58 seconds
with three Windows descriptor skips and 22 infrastructure tests selected separately. Both the
Git encoder and draft provider achieved 100% statement/branch coverage; all coverage gates passed.
The selected fault-injection run remains separate. Publication stays denied pending original G1-G7.

The completed fixed-source check exited successfully: all 75 selected invariant-breaking mutants
were killed, including removed base-tree and result-tree binding checks. Final static checks and
the 765-member source/distribution pattern scan passed with zero findings. This is component
validation, not an independent penetration test or complete operational release qualification.

### Governed external-effect reservations

The Git byte-binding source `4fa5a8df272153266d9dc10af62ba720291eec83` passed every job in
[CI 36287363677](https://github.com/kadubon/checkedflow/actions/runs/36287363677). That run predates
this state-machine increment and cannot qualify its new required infrastructure case.

V2 now retains separate effect preparation, quorum authorization, exact executor reservation,
provider observation and quorum reconciliation. Dispatch reservation consumes the full modeled
ceiling. Expiry, lost eligibility and unknown outcomes cannot release the charge or authorize
another attempt. Reconciliation retains the original provider number; contradictory object IDs
reject. Candidate withdrawal preserves a compensation obligation, and resume refuses unresolved
external operations. The bounded records pin their funding/candidate dependencies during archive
attempts. Effect retirement and compensation execution remain incomplete.

A strict GitHub intent resolver connects the approved digest, accepted result tree/contract,
executor revision and expiry to the existing byte-bound provider plan. An audit against the actual
repository-reuse path corrected the initial assumption that a candidate artifact was a patch digest:
that field is the resulting source-tree digest. Patch bytes are independently bound through the
contract. The core remains independent of network, storage, time and provider credentials.

Source tests exercise the signed lifecycle, SQLite replay, malformed snapshots, purpose/quorum
checks, conservative budgets, duplicate intents, expiry, recovery and generated observation
sequences. Agent mission inspection includes effects, and original-byte nonce recovery accepts
purpose-scoped executor commands. Dedicated real four-node/gVisor qualification now includes
reservation expiry during one-node death, catch-up, governed observation, withdrawal and common
state hashes. It uses a signed provider observation fixture, not a GitHub response. The required
installed report grows from 25 to 26 named cases; previous reports cannot satisfy it. Full-source,
mutation, installed-artifact and hosted results for this increment remain pending.

The first full run exposed stale embedded state-schema content in the operational configuration
schema (844 tests passed, one failed). Updating the embedded contract fixed the failure; a
source-independent installed smoke now also compares both schemas. The complete rerun passed
845 tests in 217.20 seconds, with three Windows descriptor skips and 23 separately selected
infrastructure tests. All authoritative coverage gates passed; the effect core and GitHub intent
resolver both reached 100% statement/branch coverage. Selected mutation results are recorded
separately after completion.

Wheel `34f62716f8c362f9df79498dd0516d59df341addaf75dec4d155d4091b318637` and sdist
`393277a65c40b7ff5e871c36233f5c69212be39b3b2ae4bd66a78ddaa22af650` passed isolated
installation and replayed the packaged signed effect vector without a source checkout. The
source/distribution publication pattern scan checked 788 members with no findings. Documentation
updates after that build do not change these package files. These checks do not qualify hosted
26-case infrastructure, separate-host deployment, full actuator recovery/compensation, or G1-G7.

Fault injection initially showed that the expiry-only retry test did not distinguish a removed
first-reservation guard: a secondary state validator still rejected the expired record. The test
now also tries to repeat a live reservation and verifies that its state hash/deadline cannot change.
That focused regression passed, and the completed rerun killed all 78 selected mutants, including
live re-reservation, unresolved resume and quorum-intent binding. No production validation was
weakened to make the mutation gate pass. Final static checks and package/source equality checks
also passed. Hosted 26-case and full operational qualification remain separate pending evidence.

### Installed reservation qualification and final provider check

The qualification job for source `d7a0d73a1e03bc5b04e0d4a352fd79afe0afb5c8`
completed all 26 required installed-wheel cases without failures, errors or skips. Its downloaded
JUnit report passed the independent qualification gate. This includes the four-node reservation
expiry, recovery, reconciliation and candidate-withdrawal case. Its provider observation remains
a signed fixture; it does not establish real GitHub execution or G1-G7 completion.
The built wheel digest is `9eaf74a677f9cc19c5501e090762c612a6c1e7ad4d324edbbaa582b31d040474`;
the sdist digest is `e40f83fef4c2682ba082663c4391084c11bf0ea2f046188afc5ee555120760a5`.
These artifacts precede the following provider change.

The provider now accepts a trusted final `before_send` check after remote preflight and durable
claim persistence, and rechecks its local enabled flag before creating a draft. Callback rejection
or failure prevents POST while retaining the unknown claim. A later caller cannot bypass that
decision by omitting the callback. The source regressions cover all four rejection classes,
successful ordering, existing observations, local disable, unexpected exceptions and forwarding
through complete-source patch binding. Maintained supervision, policy loading, staging,
compensation and complete operational qualification remain separate unfinished requirements.

The final-send increment passed the complete local check: 854 tests, three Windows descriptor
skips and 23 separately selected infrastructure cases; all static/coverage gates passed and
all 80 selected fault mutants were detected. Its wheel
`146f6bcbc812d47635ddae6911ee68c823004bd950f2d7198c828e8b780f3d9e` and sdist
`ddbf3b20b7ff016277ea37819492f2af8cc7f49640b29d54003b38a58a4daf4c` passed
isolated installation. Publication pattern scanning covered 788 source/distribution members
without findings. The preceding source's CI36289212339 finished successfully across all jobs,
including Windows/Linux Python 3.12–3.14. That CI predates this hook and does not qualify it.
