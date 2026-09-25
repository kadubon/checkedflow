# Verification scope and gates

The implementation distinguishes inspection, deterministic tests, distributed execution and
isolation qualification. Passing one group does not substitute for another.

| Gate | What it checks | Entry point |
|---|---|---|
| Static | Ruff, strict mypy, Bandit, dependency direction, core nondeterminism, schema freshness, local documentation links, actionlint | `scripts/check.py` |
| Contracts | Signatures, nonces, duplicate keys, unknown versions, bounds, evidence binding, authority, expiry and reuse | `tests/test_core.py`, `tests/test_boundaries.py` |
| State model | Generated operation sequences against independent budget state; replay, idempotency and residual preservation | `tests/test_core.py`, `tests/test_residuals.py` |
| Fault injection | Quorum, deadline, budget reserve, fencing and copy accounting mutations must fail existing tests | `scripts/fault_injection.py` |
| ABCI/storage | Invalid proposals, conflicting leases, transient state, commit boundaries and restart | `tests/test_abci.py` |
| Four nodes | Actual formation/reuse, concurrent leases, stop, TCP link partition, quorum loss and recovery | `tests/test_integration.py` |
| Isolation | Infinite work, process multiplication, excessive output, blocked stdin, path traversal, network and checker writes | `tests/test_integration.py` |
| Distribution | Wheel/sdist content and fresh installation outside checkout; Python 3.12–3.14 on Windows/Linux | `scripts/package_check.py`, CI matrix |

The static gate also checks bundled CometBFT `.proto` byte hashes against the pinned manifest
and resolves local Markdown heading links. It runs offline; external URLs are not treated as
verified by that link check. A state schema is a structural contract, not a proof of valid history.

Audit regressions cover empty-block replay, shared-ancestor traversal, streaming journals with
more than 1,024 rejected transactions, malformed archive records, lost generation-finish replies,
non-reexecution of started attempts and malformed worker specifications. The four-node fault test
also submits a signed transaction whose base64 HTTP body exceeds the old default RPC limit.
See [test_core.py](../tests/test_core.py), [test_transport.py](../tests/test_transport.py) and
[test_worker_recovery.py](../tests/test_worker_recovery.py).

The process-exhaustion case first proves that the same sandbox profile can run a normal Python
command. The attack must then produce an unknown result with bounded output and known cleanup,
through timeout or resource-failure exit 2/137. A Docker launch failure (125) does not qualify.
The assertion tests containment; it does not require an exhausted sandbox to remain available.

Core statement coverage and branch coverage must each be at least 95%. Combined coverage alone
is insufficient. Selected fault injection is a regression measure, not an exhaustive mutation
analysis or a formal proof of all transitions.

Run required infrastructure qualification on a prepared Linux host:

```sh
export CHECKEDFLOW_IMAGE='python@sha256:YOUR_DIGEST'
export CHECKEDFLOW_COMETBFT='/absolute/path/to/cometbft'
export CHECKEDFLOW_REQUIRE_INFRA=1
uv run pytest -m qualification --junitxml=reports/qualification.xml -q
uv run python scripts/qualification_gate.py reports/qualification.xml
```

Without infrastructure configuration, these tests are explicitly skipped during ordinary local
collection. Required qualification converts missing prerequisites to failures and the publication
gate rejects skipped tests, failures, errors, incomplete case lists. The workflow installs the exact built wheel and runs qualification afresh.

The partition test closes and refuses all incoming and outgoing validator TCP relay links for
one node without changing the host firewall. It does not emulate every asymmetric packet-loss or
Byzantine equivocation pattern. Invalid proposals are rejected by the live ABCI endpoint; correctness of Byzantine agreement itself
is delegated to CometBFT under its documented assumptions.

See [validation status](validation-status.md) for actual observations and remaining limits.
Do not describe an unexecuted matrix entry as supported by a passing test.

## Agent interoperability gate

[Agent tests](../tests/test_agents.py) exercise the official A2A client against the JSON-RPC ASGI
application and the official MCP client against real stdio subprocesses, with both modern
discovery and legacy initialization. Cross-protocol tests verify shared state, signature rejection,
mission isolation, exact-envelope idempotency, malformed JSON/numeric rejection, HTTP body limits,
revoked reuse, absent commit confirmation and lost-response recovery without automatic dispatch.
Core coverage thresholds remain separate from adapter protocol coverage.

Required Linux qualification includes `test_agent_protocols_share_four_node_commit`: A2A sends
a signed task to one actual CometBFT node; a separate CLI MCP process queries and resubmits it
through a different node; all four nodes must retain one task and agree on a common committed
hash. The qualification gate requires this eighth case as well as the original seven.
A2A HTTP framing uses an ASGI transport in these tests; TLS, external proxies and arbitrary
third-party hosts are not qualified by that particular case. The expanded protocol suite separately
uses real loopback TCP for A2A JSON-RPC/HTTP+JSON streams and authenticated gRPC, and for MCP
Streamable HTTP/legacy SSE. It checks durable cursor ordering/restart, signed cancellation,
push CRUD and DNS pinning, template reads, prompts, completion, subscriptions and OAuth.

Package checks first install base wheel/sdist without extras, then install `agents,distributed`
and exercise A2A discovery and MCP tool/resource discovery outside the checkout. This detects
missing packaged modules/resources and incompatible optional dependency metadata independently
of source-tree tests. No external runtime execution occurs during discovery.

The [conformance matrix](conformance.md) lists exact protocol roles. The publication scanner is
part of every static run and examines distributions separately after building. It detects personal
local paths, recognizable credentials and private deployment members without printing secret
values. Advisory scanning covers the locked runtime extras before uploading artifacts. Passing
these checks is bounded evidence; external OAuth issuers, TLS proxies and organizational
independence still need deployment-specific validation.
