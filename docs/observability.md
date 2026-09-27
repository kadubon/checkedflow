# Service liveness, readiness and current-state metrics

Status: development SDK and authenticated A2A/MCP HTTP integration. This is not the complete
operational monitoring or SLO qualification. Complete log/trace deployment, latency/storage measurements,
deployment probe provisioning and long-duration dashboard/alert qualification remain unfinished.

## Three different questions

| Signal | Meaning | What it does not prove |
|---|---|---|
| `live` | The process answered this request | A usable node, signer, store or sandbox |
| `readable` | This observation obtained a valid scoped own-node snapshot | Recent committed progress or permission to execute |
| `ready` | Readable snapshot, progressing fresh running node and every configured role probe passed | A current task fence, accepted candidate or authorization for a particular action |

A paused gateway can remain live and readable while protected-work readiness is false. It still
serves authorized historical reads and administrative commands. A readiness observation never
cancels an in-flight action or replaces the dispatcher's current authority/evidence checks.

`Monitor` uses the existing [local watchdog](dispatch-watchdog.md), including cold-start progress,
monotonic deadlines, rollback inhibition and local stop. It checks freshness again after dependency
probes, so a slow probe cannot extend the node observation. Failed reads omit state gauges entirely;
they do not publish a stale height or silently replace unavailable measurements with zero.

## Role-specific probes

The operator supplies exactly the listed read-only, bounded probe callables. Missing or extra
probe names reject configuration. A probe passes only when it returns the boolean `True`.
Ordinary exceptions produce a failed dependency without exposing their text; process interruption
still propagates. Names and metric series are fixed, not derived from client input.

| Service role | Required dependency probes |
|---|---|
| `gateway` | `configuration`, `storage` |
| `worker`, `verifier` | `configuration`, `storage`, `signer`, `sandbox` |
| `effect_executor` | `configuration`, `storage`, `signer`, `provider` |
| `validator` | `configuration`, `storage`, `signer`, `backup` |

Each deployment must define what those probes inspect: protected current configuration and key
bindings, required journals/artifact views, signer metadata, sandbox recovery readiness, provider
enablement/credential availability, and the required backup/checkpoint. Candidate code and clients
cannot supply probe implementations. The monitor does not provision these dependencies or validate
the operator's implementation of each probe. No default always-successful probes are installed.
Probes must not sign, execute candidate code, mutate provider state or contact unapproved endpoints.
They must enforce finite I/O deadlines; this synchronous SDK cannot interrupt a blocked custom probe.

```python
from checkedflow.observability import Monitor

# own_node_watchdog and read_only_probes are protected service configuration.
monitor = Monitor(own_node_watchdog, "gateway", read_only_probes)
observation = monitor.observe()
record = observation.record()
text = observation.prometheus()
```

The record follows `checkedflow schema service-observation`. Consumers must additionally check
that `ready` is exactly `readable and all(checks.values())`. A record supplied by an untrusted
client is not proof of service state or execution authority.

## Authenticated HTTP integration

The programmatic A2A `create_app`/`serve` and MCP `create_http_app`/`serve` functions accept
`monitor=monitor`. Monitoring is opt-in; existing applications remain unchanged. The CLI does not
yet provision a complete role probe set. The factory requires an authenticated mission policy with
the same chain and mission as the watchdog. Every status request passes the existing policy guard,
including its response-time revocation check. No monitoring route may be configured as public.

| GET route | Response |
|---|---|
| `/healthz` | HTTP 200 with `live` and service role, without probing dependencies |
| `/readyz` | Portable observation; HTTP 200 if ready, otherwise 503 |
| `/metrics` | Current metrics; HTTP 200 also when readiness is false |

Other HTTP methods return 405. An unauthenticated request returns 401 and a revoked or out-of-scope
client returns 403. Existing TLS and bind policies still apply. No listener, exporter, telemetry
connection or polling thread is started by the monitor itself. Normal protocol routes continue
through their original handlers. gRPC health services are not added by this HTTP integration.

## Metrics and interpretation

Text follows the [Prometheus 0.0.4 exposition format](https://prometheus.io/docs/instrumenting/exposition_formats/),
with declared gauge types, one sample per fixed name and a final LF. No task IDs, digests, mission
names, URLs, credentials or arbitrary error strings become metric labels. One monitor belongs to
one configured mission; scraping/instance labels remain the operator's responsibility.

All names have prefix `checkedflow_`. Current state gauges include:

- `committed_height`, `request_epoch` and `active_request_receipts`.
- Available ordinary/administrative request slots and bytes, plus task/candidate/effect slots.
- `budget_spent_units`, `budget_reserved_units`, `budget_available_units` and
  `budget_protected_verification_units`: modeled units, not observed money or CPU usage.
- `tasks_<status>`, `effects_<status>`, `verification_backlog` and `accepted_candidates`.
- `tasks_lease_expired`, including retained tasks whose reason is lease expiry.
- `unresolved_effects_oldest_blocks`: maximum committed-height distance from reservation among
  active unresolved effects; zero only when none exist. This is not wall-clock age.
- Mission mode, `process_live`, `node_readable`, `protected_work_ready` and
  `dependency_<fixed-name>_ready` flags.

Task/effect counts describe the bounded active state, including retained terminal entries.
Archival can reduce these gauges. They are not historical event counters or lifetime throughput.
Repeated scraping, decoding/replay and monitor restart do not increment authoritative events.
Fields not yet instrumented, such as residual age, node lag relative to another node, latency
distributions, physical storage headroom and observed metering, are omitted rather than fabricated.

When readiness fails, inspect the named dependency and the protected service log. Do not delete
journals, reset unknown effects, extend leases or bypass a stopped watchdog to clear the metric.
When ordinary capacity approaches zero, pause new admission and perform governed archival while
preserving administrative headroom. A failed scrape is distinct from a returned readiness-zero
sample; configure both conditions in the eventual deployment alert policy.

Tests cover role requirements, strict probe results, cold/stalled/paused states, expiry during
probes, unavailable reads, replay-stable gauges, exception privacy and both actual ASGI protocol
factories. Revocation during a probe blocks response egress. These tests do not establish live
managed-signer/sandbox probe correctness, multi-host availability or achieved SLOs.

Local worker/effect/service invocations can opt into [bounded operation logs and optional traces](telemetry.md).
These observations are lossy diagnostics, not committed events, task acceptance or execution authority.
No outbound exporter is enabled by default.

Use the [monitoring templates and response runbook](monitoring-runbook.md) for packaged alert rules,
dashboard queries, explicit SLI denominators and recovery targets. Templates and targets are not
proof of an installed monitoring service or achieved availability.
