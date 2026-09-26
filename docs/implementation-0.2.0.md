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
| M1 | Separate v2 contracts, legacy replay, administrative reserve | M0 | NOT_STARTED |
| M2 | Local/S3 storage, archive, checkpoints, snapshots, migration | M1 | NOT_STARTED |
| M3 | Repository patches and independent bounded verification | M1–M2 | NOT_STARTED |
| M4 | Role separation, scoped access, managed signer, rotation | M1–M2 | NOT_STARTED |
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

## Resume order

Complete baseline checks and fixed published-runtime replay captures. Implement the v2 signed
boundary and bounded state before connecting storage, identity and worker changes. Update individual
ledger entries only when their evidence exists. Keep all G1–G7 gates unqualified until their exact
required cases execute against the final artifacts. Never promote the ledger itself to PASS evidence.
