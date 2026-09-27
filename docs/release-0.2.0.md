# 0.2.0 experimental release scope

On 2026-09-28 the owner explicitly requested early release and PyPI publication with minimal
validation. This supersedes the original requirement to finish the entire operational G1–G7 profile
before publication. The frozen specification and earlier failed/incomplete evidence remain intact.
This is a scope decision, not a declaration that missing tests passed.

## Included behavior

The package retains the v1 bounded integer-array reference runtime and adds a separate v2 path for
repository patches, durable worker and external-effect records, scoped A2A/MCP access, artifact
storage, managed signing, retention, migration preparation and operator deployment tools.
Install with `pip install checkedflow==0.2.0`; use the documented optional dependency groups for
agent transports, distributed execution, S3 storage, external effects and telemetry.

The v1 and v2 wire profiles remain distinct. Existing signatures are not translated into new
authority. Application semantic version 0.1.0 in the legacy ABCI adapter identifies that retained
protocol implementation; it is not the installed distribution's version. There is no automatic live
migration or promise that all unfinished operational workflows are supported end to end.

## Minimum publication checks

The manual tagged workflow builds wheel and sdist once, checks metadata and installation outside
the checkout, scans distribution contents and dependency advisories, and runs the existing required
Windows/Linux Python 3.12–3.14 checks, actual CometBFT/gVisor infrastructure tests, managed signer
tests and real S3-compatible storage tests. The publication job depends on all of these jobs.
Skipped or failed required jobs do not authorize publication. The tag, metadata and tested artifact
hashes must match; PyPI receives the same artifacts without rebuilding. OIDC remains confined to
the `pypi` environment's publication job. No runtime permission or verification check is relaxed.

Existing evidence includes 1,284 local regression passes and 122 detected invariant mutations on
the preceding development source, [four-VM deployment application](evidence/deployment-apply-20260928.json)
and a [full fixed workload measurement](evidence/longevity-full-20260928.json). Those earlier artifacts
are not the 0.2.0 distributions and do not substitute for the tagged workflow's artifact checks.
The earlier workload job succeeded while its containing workflow was cancelled; that distinction
is preserved. No fresh long-duration or four-VM run is required by this narrowed release scope.

## Explicit limitations

- The full original G1–G7 operational profile is incomplete. No production-readiness, independent
  administration, achieved availability target or complete multi-host qualification is claimed.
- Cross-host signer custody, key-service outages, node replacement, coordinated backup restoration,
  rolling upgrades and migration with unresolved live work require further qualification.
- Complete effect compensation/retirement and deployment recovery remain unfinished. Preserve
  unknown outcomes and journals; do not repeat an external operation merely because its reply is lost.
- Separate local VMs share a physical host and operator. They do not establish independent
  organizations or all production failure domains. Monitoring observations do not authorize work.
- Generated code still requires the configured Linux/gVisor isolation; there is no host fallback.
  A2A/MCP access does not bypass signatures, budgets, admission or independent verification.

Use disposable environments for evaluation, retain keys/state/evidence, and review the relevant
operator guide before connecting external services. The Alpha package classifier is retained.
Later work can qualify a specific operational profile without rewriting these historical results.
