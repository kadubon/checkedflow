# Bounded longevity measurement

Status: **implementation and short-path qualification pending; not a G5 result**. The packaged
[profile](../src/checkedflow/data/load-profile.json) fixes the workload before execution. A successful
short smoke test is not evidence that the full profile meets its limits. Do not shorten or otherwise
modify the full profile after observing a failure; preserve that run and declare a different profile
for any subsequent experiment.

## Fixed workload and scope

- Submit 4,097 distinct signed control requests at 500 ms phase-relative intervals. These are
  control admissions, not completed tasks or verified-work throughput. One consumer preserves
  every scheduled intent; delays create a measured backlog rather than dropped requests.
- Process 65 byte-distinct invoice patches at 30-second intervals in the next phase. A comment
  distinguishes each source tree; their intended behavior is identical. No novelty or capability
  increase is claimed. Three organizational verifier identities execute each patch through actual
  gVisor and attest to independently observed fixture outputs before acceptance is counted.
- Preserve ordinary/governance journal headroom through explicit pause/rollover/resume. Revoke
  and retire each completed candidate and its settled funding; retain every history root and verify
  the actual four SQLite journals and archive objects after shutdown.
- Crash and restart one node with its existing signing state. Confirm renewed committed progress,
  compare common-height hashes and reject an old-epoch command after repeated rollover.
- During the designated candidate, witness an owned sandbox running, commit drain, reject new
  funding and settle the existing task before resuming. Also measure an empty drain after completion.
  These checks do not establish physical replica independence or complete G5/G6/G7 qualification.

The active workload has a 5,400-second deadline and cleanup/replay a further 600 seconds. The
profile bounds sampled controller/node RSS and test-directory bytes to 2 GiB each, application
state to 4 MiB and active receipts to 144. These are measured component limits, not hardware
recommendations. Record CPU count, guest memory, kernel, image digest, CometBFT digest, profile
digest and exact wheel identity before work begins. Use an approved disposable Linux host with
an independently enforced lifetime beyond cleanup; a killed interpreter cannot guarantee its own
cleanup. Do not share the lab's ports or run tests concurrently with pytest-xdist.

## Running the full profile

Provision the pinned runtimes described in [operations](operations.md), install the exact candidate
wheel plus its distributed dependencies and pinned test dependencies in a fresh environment, and
retain the wheel for byte comparison. Do not use an editable source installation. The runner
compares every packaged installed member with that wheel before creating the laboratory.

Set these protected operator inputs; paths below are examples, not production defaults:

```sh
export CHECKEDFLOW_REQUIRE_INFRA=1
export CHECKEDFLOW_IMAGE='python@sha256:<approved-image-digest>'
export CHECKEDFLOW_COMETBFT='/opt/checkedflow/cometbft'
export CHECKEDFLOW_LOAD_WHEEL='/protected/dist/checkedflow-0.2.0-py3-none-any.whl'
export CHECKEDFLOW_LOAD_REPORT='/protected/reports/load-run-001'
/opt/checkedflow-test/bin/python -m pytest tests/test_longevity.py -m longevity -q
```

Use the candidate wheel's actual filename; current development artifacts still carry 0.1.0
metadata and must never replace public 0.1.0. The report directory must not exist. Missing Linux,
gVisor or CometBFT fails with `CHECKEDFLOW_REQUIRE_INFRA=1`; a skipped run does not qualify.
Keep the test harness at the same reviewed source revision as the artifact. The original manifest
is retained even if a later stage fails. Preserve failed and incomplete runs.

## Reading the records

`manifest.json` contains the pre-run identity and machine envelope. `result.json` distinguishes
failure from `COMPONENT_MEASURED` and always states `not_release_authority: true`.
`commands.json`, `arrivals.json`, `accepted.json`, `resources.json` and `archive_roots.json` retain
the observations behind the summary. A missing result is incomplete, never a pass.

Latency quantiles use nearest-rank p50/p95/p99 in nanoseconds. Missing samples produce null,
not zero. `control_completed / control_elapsed_ns` and
`verified_completions / verified_elapsed_ns` are separate rate numerators/denominators; multiply
by one billion for per-second rates. Recovery time includes the declared interruption/progress
barriers. Disk growth covers the whole test directory, including consensus history and control
work; dividing it by verified completions is an amortized experiment cost, not a marginal task size.

Resource snapshots occur every 32 control intents and after each accepted candidate. RSS covers
the controller and live owned node processes, excluding short-lived sandbox processes; it is a
sampled observation rather than a proven peak. The full operational memory,
replica-loss and longevity envelope still needs its separate evidence. Do not cite these records
as a release gate before that scope is completed and audited.

## Short CI path

The required infrastructure suite includes `test_installed_longevity_path_smoke`, with a separately
named profile, 17 control intents and two candidates. It exercises rollover, actual patch checking,
retirement, replay and report creation without pretending to cross the long-profile thresholds.
The full `longevity` marker is opt-in and is excluded from ordinary unit/infrastructure runs.
