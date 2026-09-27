# Four-VM component experiment, 2026-09-26

Status: **TESTED_AS_INSTALLED_ARTIFACT for the component cases below**. The operational profile
and G6 remain unqualified. The owner authorized disposable Linux environments; lack of an
approved environment is no longer the current obstacle. Remaining implementation and qualification
work are tracked in the [implementation ledger](implementation-0.2.0.md).

## Environment and artifact identity

Four QEMU/KVM guests have separate Linux kernels, boot identities, machine identities and writable
disks. Each has two virtual CPUs, 4,096 MiB RAM and a 20 GiB virtual disk. All share one Windows
physical machine, one WSL2 host, a virtual peer network and one administrator. This tests separate
guest failure domains, not independent hardware, networks, organizations or adversarial operators.
SSH management binds only to host loopback; consensus peers use the private guest network.
Application RPC and ABCI bind to guest loopback. No new public listener is configured.

The initial VM session is bounded to two hours. Application services have a separate 30-minute
maximum lifetime, with no automatic restart or boot enablement. The controller owns its SSH
tunnels and closes them after each experiment. Guest Docker configuration is confined to the
disposable VMs; the host Docker daemon is unchanged. No paid cloud resources were provisioned.

| Item | Observed identity |
|---|---|
| Package source | `f695b870311e44733aabb90c6d2f64d98a1b8365` |
| Package build | [CI run 36230700551](https://github.com/kadubon/checkedflow/actions/runs/36230700551) |
| Wheel SHA-256 | `5688059635a43eb002a0f120307291d4365be6ac64832de26ceaaafa167d8004` |
| sdist SHA-256 from that build | `713a811fb26dc14028761b989e5e728db83f31bb894fec2473ddb0aba5fa9e6b` |
| Ubuntu cloud image | Noble, build 20260911; signed checksum manifest verified |
| Image SHA-256 | `612b2c0cc1bc413a6cb8c38fd611794caf0f2b436c50013d8b3794db12ad7354` |
| Guest kernel | `6.8.0-139-generic` |
| QEMU package | `8.2.2+ds-0ubuntu1.18` |
| CometBFT module | `github.com/cometbft/cometbft@v0.40.0` |
| Built CometBFT binary SHA-256 | `23752877fe1d856009551beca2a326296b5cf846d6bc64fd72ae35d251811d1b` |
| Docker | `29.8.1` |
| gVisor | `release-20260921.0` |

The source archive, module checksum, sandbox binaries and Python image were checked against
[runtime-lock.json](../deploy/runtime-lock.json). The CometBFT binary was built once in a guest
and the same hash checked on all four guests. The identical CI wheel was installed, with hashed
dependencies, into each guest and a separate controller environment outside the source checkout.
The package still has development **0.1.0** metadata. These files must never replace public 0.1.0.
The sdist is identified for provenance; this VM experiment installs the wheel, not the sdist.
Before shutdown, all 126 installed package members on each guest were compared byte-for-byte
against the CI wheel; all four comparisons passed and imports resolved to installed packages.

## Completed observations

- All four guests completed boot, with distinct machine and boot identities.
- All twelve directed guest-to-guest ICMP paths succeeded.
- All four guests executed the pinned Python image through actual gVisor with nonroot identity,
  no network, a read-only root, and CPU, memory and process limits. This is a preflight, not the
  complete hostile-code isolation suite.
- Four separate CometBFT/ABCI instances reached agreement using independent guest disks.
- Two concurrent worker requests yielded one committed owner and fencing value 1. The other
  response was uncertain; committed ownership, not the transport response alone, resolved it.
- After killing one node's CometBFT and application processes, the remaining three progressed
  from observed height 25 to at least 28.
- After killing a second node, the remaining two stopped at observed height 29. Eight subsequent
  successful reads at half-second intervals did not advance height or renew watchdog readiness.
- Restarting the two nodes with their original disks and signing state restored progress.
  All four reported matching tasks and budget, and the common committed application hash at
  height 32 was `4E97370C20F6CEF042743A2F7D768492EFF1BF8927279A9B57462F62C37DF254`.
  The started task remained running; no result or verifier acceptance was invented.
- The successful process-fault experiment stopped its application services and closed its
  tunnels without reported cleanup errors.

These are bounded observations, not throughput, latency, recovery-time or availability SLOs.
The task fixture did not execute candidate work or produce accepted artifacts. Its purpose was
to observe ownership, accounting and watchdog behavior across actual VM kernels.

A separate fresh experiment left the services running and disabled only the peer-network
interface in selected guests. The management interface remained available. One disconnected
node left three progressing from height 27 to at least 30. Disconnecting a second stopped
consensus at height 31; all eight half-second samples inhibited dispatch. After restoring both
peer interfaces, all four nodes agreed on tasks and budget. Their common hash at height 33 was
`05299E5B99BD40841D1BA15C5E9BB450A5CED3B3E9AA671D5EB1817B32DE0828`.
Interface restoration, service stopping and tunnel cleanup completed without reported errors.
This was actual guest network loss, not a mocked RPC exception.

Finally, all four guests were gracefully powered down and the owned virtual network helper
stopped. The disks and private evidence were preserved for continuation. No lab QEMU process
remained. Restarting this lab requires an explicit bounded session and the existing signing
state; copying or resetting validator signing state is not a recovery procedure.

## Failed attempts and corrections

The initial virtual network helper was paused, so guest reachability failed. Guests were shut
down before repairing the helper; all twelve directed paths then passed. Initial KVM device
access also failed. The actual guest processes run as an unprivileged owner with a process-local
KVM group and no-new-privileges; no permanent account-group change was needed.

Two guest bootstrap attempts timed out during Docker-group creation. Existing extracted binaries
were compared against the pinned archives before completing the partial installation. A stale
group lock was removed only after confirming its recorded process was absent. The normal password
lock was preserved. An explicit unused group ID and bounded longer account-configuration timeout
allowed setup to finish. All four real sandbox probes subsequently passed.

The first consensus experiment passed agreement and competing acquisition, then failed its
systemd fault-injection command: the main process was killed, but signaling auxiliary processes
returned an error. Cleanup also encountered an already-collected transient unit. This run remains
failed. The helper was corrected to signal the main process explicitly and inspect unit existence
before stopping it. A new experiment used fresh consensus data; the original failed record and
signing state were preserved. The successful observations above belong to that second experiment.

## Evidence custody and remaining work

Raw inventories, SSH host keys, access keys, signing keys, runtime archives, logs, independent
guest disks and failed/successful run records stay in a private local lab directory. Do not publish
that directory or attach raw inventories to an issue. Public documentation intentionally omits
private addresses, machine IDs, credentials and operator-local paths.

The bootstrap and experiment helpers are private operator fixtures, not the maintained deployment
CLI or protected release-evidence workflow required by the specification. A handwritten report or
editable ledger is not release authority. No G6 PASS or QUALIFIED_MULTI_HOST operational status
follows from these component observations.

Still required are delayed replies, storage and managed-key-service interruption, replacement,
coordinated backup restoration and upgrades, as well as the complete declared workload and its
resource measurements. Other open gates include sustainable archival, supervised end-to-end work,
consensus-bound external effects, uniform v2 access and protected exact-artifact release gating.
No v0.2.0 tag, GitHub release or PyPI publication is authorized by this experiment alone.
