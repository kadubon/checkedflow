# Security audit and threat model

The release audit reviews code, dependency advisories, artifact contents and executable boundary
tests. It is an engineering audit, not an independent penetration test or proof of safety.
Generated code, agent requests, callbacks and source/evidence text are untrusted. Organization
keys, the operator's host, Docker daemon, own validating node and approved verifier contracts
are trusted deployment inputs.

## Reviewed boundaries and controls

| Boundary | Control and regression evidence |
|---|---|
| HTTP/gRPC admission | Bearer before application dispatch; every non-discovery route protected; numeric loopback defaults; rejected browser Origin; bounded body, parse depth and concurrency |
| Signed command admission | Original UTF-8 JSON string; duplicate/float/oversize rejection; Ed25519, chain, nonce, role and quorum checks; no keys held by gateways |
| State observation | Own-node committed state and command-digest confirmation; unknown outcomes retained; exact duplicate never re-executes an effect |
| A2A cancellation | Matching signed administrative reconciliation and eligible uncertain state; transport credential cannot abandon active work |
| Pagination/history | Mission-bound persistent HMAC cursors; changes invalidate pagination; transport observation time never enters consensus |
| Push callback egress | Operator HTTPS allowlist, public-only DNS, pinned IP, original TLS identity, no redirects, no unbounded response read, bounded persistent retries |
| OAuth resource access | External issuer, exact audience, expiry, subject/client identity and scope; asymmetric public JWKS only; no token-supplied key URL fetch |
| Prompt/evidence content | Explicitly untrusted prompt content; no dynamic import, command execution or signing at agent boundaries |
| External execution | Fixed image/argv, gVisor, nonroot/read-only/no network, CPU/memory/process/time/output limits, fail-closed cleanup |
| Build/publication | Immutable action revisions, frozen dependencies, source/package leakage scan, same tested artifacts, manual tag match, OIDC only on publish job |

The audit corrected a real stream lifecycle issue: the official A2A SDK reads the first generator
item from a different asynchronous task. A task-owned capacity limiter therefore failed on release.
A bounded semaphore now spans SDK iterator handoffs, verified through real TCP streaming on both
HTTP bindings. It also identified protocol details that needed explicit tests: inclusive timestamp
filters, optional versus scalar pagination fields, mission-bound cursor persistence and SDK error
metadata. These fixes do not alter the core command format.

## Reproduce the automated audit

```sh
uv run python scripts/check.py --actionlint /absolute/path/to/actionlint
uv run python scripts/security_audit.py --dist dist
uvx pip-audit --path .venv/lib/python3.12/site-packages
```

On Windows the audit path is `.venv/Lib/site-packages`. The release workflow exports all runtime
extras with hashes and audits those locked third-party requirements. The local review of the
development environment found no reported advisories in installed third-party packages on
2026-09-25. CheckedFlow itself was not yet published at the time of that dependency scan and
was excluded by the advisory service;
its code is covered by source inspection, Bandit and regression tests. Advisory results are a
time-limited database observation, not a guarantee that no vulnerability exists.

The publication scanner examines the explicit source trees and wheel/sdist members. It rejects
personal home-directory paths, common provider credential formats, private-key PEM markers,
private deployment filenames and unsafe archive members. Findings disclose only locations,
never matched credentials. Known test-only keys are deterministically generated in fixtures;
they cannot be used as real deployment identities. Generated laboratory keys, SQLite databases,
virtual environments, downloaded tools, raw reports and local caches are excluded from releases.

## Deployment limits

One gateway admits one mission's full visibility. It has no per-record or per-client ACL within
that mission. Use distinct credentials/processes for separate visibility boundaries. Reverse
proxies/TLS, external OAuth issuers and real webhook receivers require deployment qualification.
Protect the journal directory and sidecars with OS permissions, especially Windows ACLs; push
credentials are stored there. An HTTP token permits inspection and callback configuration but
never grants transaction-signing authority.

Runtime and transport bounds constrain resource use, but they are not general denial-of-service
protection. An authorized mission can exhaust finite state; streams can consume connection
slots. Apply rate/concurrency limits at the operator boundary and plan successor deployments.
Push callbacks may duplicate after a crash and stop after three failed attempts. Inspect/reconcile
delivery and work independently. The sandbox cannot protect a compromised host or daemon.

Four colocated test nodes do not demonstrate organizational independence. A negative verifier
vote conservatively blocks artifact reuse; one-fault consensus progress does not ensure artifact
availability. The [research claims](research.md) and [validation limits](validation-status.md)
remain narrower than general correctness, intelligence growth or external truth.
