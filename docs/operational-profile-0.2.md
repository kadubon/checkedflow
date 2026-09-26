# Proposed 0.2 operational profile

Status: **UNQUALIFIED**. This document is a target envelope, not a supported-production claim.

## Product and trust group

The target is a fixed-membership four-operator runtime for bounded Python repository patches.
Each operator owns a separate Linux node environment, a consensus signer with anti-double-sign
state, separate application administration/worker/verifier keys, and authenticated transport.
Three distinct organizations authorize governance and acceptance. Consensus ordering and checker
acceptance remain independent. A negative verifier observation remains visible and may quarantine
an artifact even after three positive observations.

Validators can read replicated application state. Client ACLs do not provide confidentiality from
those operators. Separate confidential compartments require separate deployments and storage scopes.
Managed signing services, host kernels, gVisor, checker implementations, storage operators and
destination GitHub policy remain trusted components; the system does not establish their honesty.

## Profiles

| Profile | Scope | Claim |
|---|---|---|
| SDK/offline | Inspect and replay signed histories | No distributed finality |
| Laboratory | Four nodes on one disposable Linux host | Mechanism qualification only |
| Operational | Four separately approved Linux environments | Requires G1–G7, including real multi-host faults |

Operational nodes require pinned CometBFT, Docker with gVisor, an approved S3-compatible store,
a non-exporting managed signer and authenticated private connectivity. No services are exposed
publicly by default. Provisioning or firewall changes require an explicitly authorized inventory.
No ordinary-Docker or host-subprocess candidate execution fallback is allowed.

## Proposed bounded engineering workload

The initial longevity workload will use deterministic fixture patches, bounded inputs and no LLM.
Before qualification, freeze the actual machine inventory, image/binary digests, arrival schedule,
duration, concurrency, byte ceilings and cleanup plan in the run manifest. Cross more than 4,096
accepted request identities and 64 artifact identities over the lifecycle; retain bounded active
state, governance headroom, budget conservation and retired-request rejection.

No throughput, latency, RPO or RTO result has yet been measured for this profile. Targets and actual
results must be separate fields. Operational readiness requires actual failure/recovery measurements,
not inherited 0.1.0 results. A missing multi-host inventory blocks qualification, not implementation.

The owner subsequently authorized a [four-VM local laboratory](vm-laboratory-2026-09-26.md).
It supplies separate Linux node environments on one physical host. It does not establish independent
operators or complete G6; the deployment lifecycle and remaining fault cases still require work.

## Exclusions and threats

No dynamic organization membership, arbitrary repository execution, general program correctness,
secret hidden tests, exactly-once GitHub effects, automatic merging, privileged candidate CI,
arbitrary remote fetch, private computation or proven AI acceleration is promised.

Threats to cover include hostile patch paths and archives; forged test reports; stale or withdrawn
evidence; cross-scope reads and callbacks; compromised/rotated keys; lost replies and duplicate
dispatch; artifact outage/corruption; exhausted state headroom; snapshot substitution; rollback
of validator signing state; quorum loss and mixed-version deployment. Required cases are in the
[specification](specification-0.2.0.md); implementation evidence belongs in the
[ledger](implementation-0.2.0.json), with actual gate results stored separately.
