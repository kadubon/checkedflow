# Validation record — 2026-09-25

This record separates local observations from the mandatory release workflow. Published artifacts
must pass fresh checks in the same [workflow](../.github/workflows/workflow.yml) that uploads them.
Historical reports, unavailable infrastructure and skipped tests cannot authorize publication.

## Local audit baseline and final regression

| Environment | Python | Unit, contract, property and adapter tests |
|---|---|---|
| Windows | 3.12.10 | 157 passed |
| Windows | 3.13.3 | 157 passed |
| Windows | 3.14.6 | 157 passed |
| Linux (WSL2) | 3.12.3 | 157 passed |
| Linux (WSL2) | 3.13.13 | 157 passed |
| Linux (WSL2) | 3.14.6 | 157 passed |

All six runs excluded the eight infrastructure cases deliberately; those are a separate required
gate. Ruff, formatting, mypy strict, Bandit, architecture/determinism inspection, schema freshness,
document links/anchors, protocol source hashes and actionlint passed. Core statement coverage
was **100% (465/465)** and branch coverage **98.53% (67/68)**. Five selected mutations of quorum,
expiry, verification capacity, fencing and duplicate accounting were detected. This is bounded
test evidence, not a correctness proof.

After the final RPC outcome regression was added, the Windows 3.12 source check passed
**158 tests** with the same coverage and static/fault checks. The table preserves the earlier
six-environment local baseline; the tagged release workflow reruns the final source on all six
Windows/Linux and Python 3.12–3.14 combinations.

The final agent suite includes 29 gateway/initial protocol cases and 43 expanded protocol cases.
The latter cover all A2A operations, real TCP JSON-RPC/HTTP+JSON streaming, authenticated gRPC,
durable journal restart and clock rollback, cursor binding, cancellation quorum, push policy,
DNS pinning, in-flight callback replacement, MCP HTTP/SSE, templates, prompts, completion,
subscriptions and OAuth claim/signature/key-rotation behavior.

## Infrastructure gate and acceptance evidence

The mandatory eight-case qualification runs real CometBFT nodes and gVisor: full formation/reuse,
cross-protocol access through different nodes, competing leases, invalid proposals, a stopped
node, actual TCP partitions, quorum loss/recovery, resource exhaustion and sandbox network/write
restrictions. ABCI unit tests separately exercise pre/post-commit crash recovery. The release
workflow installs its **built wheel** before qualification and rejects missing, skipped, failed
or errored required cases.

The retained [acceptance report](acceptance.json) is a local source-run observation. It records
agreement at a common committed height and hash, three distinct behaviors in each comparison
condition, 780 charged work units each, zero outstanding reservations and two reuse calls.
Search examined 23 grammar candidates with reuse versus 55 from scratch, including formation.
Round 2 was worse with reuse (10 versus 8); that result is retained. Fresh keys and block timing
change heights/hashes across runs; all nodes must agree within the same run.

External versions are pinned in [runtime-lock.json](../deploy/runtime-lock.json): CometBFT module
v0.40.0, gVisor release-20260921.0 and the immutable Python image. Local Docker was 29.8.1;
hosted CI supplies its Docker engine and verifies runsc with a preflight before tests. No host
execution fallback is permitted. The local laboratory's initial image lookup failed; the same
pinned image was restored before qualification, without counting that lookup as a test pass.

## Failures found and corrected

The original audit corrected empty-block replay, missing generated-source recovery, malformed
worker-result handling, encoded transaction limits and exponential ancestor traversal. The first
agent qualification exposed CometBFT rejecting an already committed transaction in its duplicate
cache; the gateway now acknowledges the authenticated committed digest without rebroadcast.

Expanded streaming tests exposed a task-owned semaphore mismatch across the A2A SDK's iterator
handoff; a bounded semaphore now releases independently of the current async task. Linux 3.13
also exposed the SSE dependency's process-wide shutdown watcher affecting sequential test
servers. Test fixtures now isolate signal handling and bound shutdown, while real transport and
signature checks remain active. The six-environment rerun passed. Malformed inner envelopes now
receive schema errors before projection rather than escaping as missing-key exceptions.

The [security audit](security.md) also covers callback replacement races and publication leakage.
One later local qualification ran seven cases successfully but failed the formation case when
CometBFT timed out waiting to notify a committed start transaction. The RPC boundary now labels
such errors `OUTCOME_UNKNOWN` without retry; the laboratory wait was raised from 10 to 20 seconds
within the client's 30-second limit. A dedicated regression checks this classification. Release
qualification must rerun all eight cases; that failed run is not treated as a pass. The subsequent
local source qualification executed and passed all eight cases in 289.70 seconds, with no skips.
The [initial hosted workflow](https://github.com/kadubon/checkedflow/actions/runs/36147318095)
also passed its six-platform checks and all eight real infrastructure cases. Publication still
requires the final tagged source and its exact built wheel to pass a fresh workflow.
The source scan reported zero personal-path/credential findings. Locked third-party runtime
dependencies and the installed development environment reported no known advisories on the audit
date. These scanners do not detect every secret format or unknown vulnerability.

## Package and release evidence

Wheel and sdist include contracts, vectors, examples, type information and license attribution.
`scripts/package_check.py` installs base and then agent/distributed extras into clean environments
outside the checkout. The six-platform workflow repeats these checks on the same build artifacts.
Source and archive leakage scans run before upload; the PyPI job checks tag, package version and
SHA-256 manifest without rebuilding. OIDC is limited to that job and the `pypi` environment.

Local raw logs are ignored under `reports/`; they can contain local tool paths and are not shipped.
For a published version, use its [GitHub release](https://github.com/kadubon/checkedflow/releases),
the linked Actions run, attached distribution hashes and PyPI attestations to identify the exact
released source/artifacts. The release gate needs fresh success, not this prose.

## Interpretation limits

The tests are a single-host laboratory. They do not demonstrate actual organizational
independence, arbitrary Byzantine schedules, general program correctness, general novelty,
causal intelligence growth or production security certification. A negative verifier can
conservatively quarantine a capability even while consensus progresses. External TLS proxies,
OAuth issuers, real webhook providers and arbitrary agent hosts need deployment qualification.
See [conformance](conformance.md) for exact protocol roles and [research](research.md) for claims
that the implementation does not make.
