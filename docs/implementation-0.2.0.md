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
| M3 | Repository patches and independent bounded verification | M1–M2 | NOT_STARTED |
| M4 | Role separation, scoped access, managed signer, rotation | M1–M2 | IN PROGRESS |
| M5 | Worker supervision, effect journal, recovery | M2–M4 | NOT_STARTED |
| M6 | Metrics, readiness, deployments, runbooks | M2–M5 | NOT_STARTED |
| M7 | Installed, infrastructure, fault, load, multi-host qualification | M3–M6 | NOT_STARTED |
| M8 | Documentation, exact-artifact publication gates, release | M7 | NOT_STARTED |

## Infrastructure dependencies and execution limits

No four-host inventory or disposable GitHub effect target has been supplied at baseline.
G6 and live-effect qualification therefore have unavailable prerequisites. Independent
implementation continues; neither mocked tests nor four colocated containers can satisfy them.
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
