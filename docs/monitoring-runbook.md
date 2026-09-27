# Monitoring configuration and response runbook

Status: packaged monitoring templates, evaluated Prometheus rules and a Classic Grafana dashboard
configuration. These files do not deploy services, issue credentials or prove operational availability.
The dashboard's queries are parsed; a live Grafana rendering is not yet qualified.

## Obtain and validate the files

The installed package includes the templates; a source checkout is unnecessary:

```sh
checkedflow monitoring alerts > alerts.yml
checkedflow monitoring alert-tests > alert-tests.yml
checkedflow monitoring scrape > prometheus.yml.example
checkedflow monitoring dashboard > dashboard.json
promtool check rules alerts.yml
promtool test rules alert-tests.yml
promtool check config --syntax-only prometheus.yml.example
```

Use a new operator-owned directory; shell redirection can overwrite an existing file. The example
scrape configuration uses HTTPS, client certificates, a CA and a protected Bearer credential file.
Replace the placeholder certificate name and paths with provisioned values. The loopback target
does not open a public listener. Expose the [authenticated monitoring routes](observability.md)
through the configured SDK service first; the current CLI does not automatically provision probes.
Use a monitoring principal with explicit mission read access. Keep alerting and dashboard interfaces
private and authenticated too. No notification receiver or external destination is configured here.

The validation baseline is promtool 3.15.0, upstream revision
`5241a27fe3c6983549fccc32f6e65917408c63cd`, Linux amd64. The fetch script checks the upstream
archive SHA-256 `2a542df32eac02ee17b9d844fb2aa1de00dafa5476579ba8a3ba862e9d572ea0` and extracted
binary SHA-256 `c736d55d3ccd959fe48329965fb5cb671d45585a9483954766448035100ff75c`.
It neither installs a daemon nor starts a server. CI checks the 11 rules, their explicit time-series
fixtures, scrape syntax and eight dashboard expressions. Syntax-only validation deliberately does
not claim certificate/key existence or a successful authenticated scrape.

Import `dashboard.json` using Grafana's Classic dashboard importer and select an operator-owned
Prometheus data source. Panels cover scrape/readiness state, height, budget, verification backlog
and unresolved-effect age. Missing data stays a gap; it is not replaced by zero or connected across
an outage. Validate the imported rendering and datasource permissions in the target deployment.
See the primary [Prometheus rule guide](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)
and [Grafana JSON model](https://grafana.com/docs/grafana-cloud/learn-and-build/visualizations/dashboards/build-dashboards/view-dashboard-json-model/).

## Thresholds and interpretation

The templates select only `job="checkedflow"`. Maintain an explicit expected-target inventory:
`up=0` identifies a failed configured target, while the missing-target-set rule detects loss of the
entire job. Removing one target from discovery requires an inventory check; no metric can infer an
operator's intended missing address. Instance labels belong to the protected scrape configuration.

All alerts require a continuous two-minute condition, evaluated every 30 seconds. Example capacity
thresholds are eight ordinary/task/candidate/effect slots, three administrative slots and 4,096
administrative bytes. The unresolved-effect threshold is 100 committed blocks. These are reviewed
laboratory policy defaults, not measured production limits or automatic authorization to archive.
Change them with the deployment's traffic, request sizes, deadlines and recovery envelope, then
rerun the rule tests. Avoid assigning task IDs, digests or user text as labels.

## Scrape

`CheckedFlowScrapeFailed`, `CheckedFlowTargetSetMissing` and `CheckedFlowObservationMissing`
separate unreachable targets, a missing job and a responding target with missing observation data.
Check target inventory, service process, TLS identity/expiry, current principal grants and the
metrics route. Preserve access checks; do not make metrics public or disable certificate validation
to clear an alert. Confirm a new authenticated observation after repair. A failed scrape is not a
proof of failed consensus, and an HTTP response alone is not readiness.

## Node progress

`CheckedFlowNodeUnreadable` means no current scoped state could be read. `CheckedFlowNodeNotFresh`
means a running mission lacks the watchdog's required progress. A paused mission does not trigger
the latter rule. Inspect all four approved nodes, their committed heights, peer reachability,
validator ownership and consensus logs. Agreement loss is one possible cause; one stale service
observation cannot establish which organization failed or prove a global quorum outage.

Stop new external dispatch during uncertainty. Keep original signed commands, journals and unknown
outcomes. Restore progressing own-node observations and current authority before resuming; do not
extend leases using wall-clock silence or reset a stopped watchdog to create permission.

## Headroom

`CheckedFlowHeadroomLow` covers bounded active-state slots. `CheckedFlowAdministrativeHeadroomLow`
covers the separate administrative reserve. Pause new admission and inspect safe settled-work
retirement, retention roots and verified archives. Keep unresolved work and effect obligations.
Deleting recovery databases, dropping unknown effects or raising bounds without contract review
is not recovery. These metrics describe logical state capacity, not filesystem bytes; separately
monitor actual disk usage, free space and inode/file limits in the deployment.

## Unresolved effects

`CheckedFlowUnresolvedEffectAge` uses committed-height distance from reservation. It is not elapsed
seconds and can stop increasing during a quorum outage. Inspect the original intent, current
governance, local disable state and retained provider observations. Use read-only reconciliation
and current administrative quorum review. Absence of a remote draft or a missing reply never
authorizes repeating the write. Compensation and retirement must preserve the original obligation.

## Backup

`CheckedFlowRequiredBackupUnavailable` means the configured required-backup probe failed. Confirm
the declared checkpoint, retention roots, readable artifact copies and an independently verified
restore path. A directory or recent timestamp alone is not a verified backup. Do not start a
restored validator with copied signing state while another owner may still run. Application replay
and validator anti-double-sign custody are separate procedures. Complete validator recovery
qualification remains unfinished.

## Signing

`CheckedFlowSigningUnavailable` reports the required signer probe, not an exact diagnosis of key
expiry. Check purpose, identity, revision, usable-height interval, withdrawal and signer service
reachability. Provision a governed replacement when needed; never load a fallback production key
from candidate storage. Test the operator's probe against expired/revoked credentials. Complete
credential-expiry metrics and alert deployment qualification remain unfinished.

## Evidence

`CheckedFlowReservedEvidenceUnavailable` reports reserved modeled budget together with failed
required storage. Inspect fresh byte-verified replicas and current retention roots. It does not
prove which particular reservation lacks evidence, nor cover every possible unbacked reservation.
Keep the reservation and suspend further execution until its exact evidence is verified. Never
replace missing bytes with an upload acknowledgement or reset modeled charges after uncertainty.

## Operating envelope and objectives

The example evaluation profile is a 30-second scrape, ten-second scrape timeout, 30-second rule
evaluation and two-minute pending period. It is not a measured CPU/memory requirement. It cannot
replace fast dispatch inhibition: the local watchdog and current authority checks act at execution.
Capacity metrics follow the configured bounded state; queue lengths and block ages are gauges,
not rates, historical throughput or financial expenditure.

| SLI | Numerator and denominator | Evidence and limitation |
|---|---|---|
| Scrape availability, per expected instance | Successful authenticated observations / scheduled observations outside predeclared maintenance | Missing observations count as failures; an empty denominator is unknown |
| Protected-work readiness, per role | Observations with readable, fresh state and all required probes passing / expected observations while the mission is scheduled to run | Do not average away a failed required role or treat readiness as task authority |
| Time to checked acceptance | Elapsed time from admitted work to the required independent acceptance, divided into declared latency buckets over all eligible admitted work | Full lifecycle timestamp instrumentation is pending; step duration is not this SLI |
| Recovery point | Verified recovered committed height and evidence roots compared with the last confirmed committed tip | Block distance is not wall-clock data loss; unknown provider outcomes remain unknown |
| Recovery time | Time from declared incident start to a verified usable recovery, including current ownership and readiness | Must be measured by the recovery exercise, not inferred from process startup |

An illustrative lab availability target is 99.5% per required role over a 30-day window. It is a
target, not an achieved SLO or contractual SLA. Exclude maintenance only when declared beforehand;
do not discard failures or unknown samples after the fact. When the declared error budget is
exhausted, pause optional changes/new work, preserve administrative recovery capacity and investigate
before expanding load. Security/integrity failures stop affected work immediately, independently
of the availability budget.

Lab recovery targets are zero lost committed operations and recovery within 15 minutes for a
single-node outage in the approved four-host laboratory. Verification requires surviving consensus
logs and the exact retained evidence. Total log/evidence loss has no zero-loss promise. Backup-only recovery is
bounded by the last independently verified checkpoint and evidence roots. These RPO/RTO targets
are unqualified until the specified final-artifact four-host drills measure them. None authorizes
unsafe validator restart, relaxed artifact thresholds or replay of unknown external effects.

Notification delivery, single-target inventory drift, physical storage headroom, precise expired-key
and unbacked-reservation detection, deployment probes, dashboard rendering and long-duration SLI
measurements still require implementation/qualification. The templates are not the complete R11
or G1-G7 acceptance evidence.
