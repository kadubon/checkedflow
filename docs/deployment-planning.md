# Deployment planning and local node services

`checkedflow deployment-plan` generates configuration and service files for four pre-provisioned
Linux nodes. It does not connect to hosts, create accounts or keys, install software, start services,
change a firewall, or move validator signing state. This is the offline planning part of the operational
deployment path. The separate `deployment-service` command operates already provisioned local
services. Managed installation, work draining, complete recovery orchestration and full multi-host
qualification remain unfinished; generated files alone do not prove a qualified deployment.

## Inputs and trust

Install the distributed extra. Supply three independently reviewed inputs:

1. An inventory using `checkedflow schema deployment-inventory`.
2. The public v2 application configuration, with its fresh initial state and four validator public keys.
3. The corresponding CometBFT genesis, with equal positive voting power and exactly the same
   chain, application state and validator keys.

The inventory names four distinct organizations in the application's order, four unique node names,
four distinct RFC1918 IPv4 addresses and four P2P node IDs. IPv6, public peer addresses and DNS
resolution are outside this first inventory profile. The organization and address labels do not prove
independent administration or physical failure domains. The generator rejects reuse of known
validator/command key identities as P2P identities; ownership and freshness still need live preflight.

`runtime` declares absolute normalized `python` and `cometbft` executable paths under `/opt` or
`/usr`, plus the exact `wheel_sha256` and `cometbft_sha256`. Paths cannot contain whitespace,
systemd substitutions, shell substitutions or traversal. These hashes are declarations at this stage:
the generator does not read remote executables, authenticate an installation or validate every native
CometBFT consensus parameter. Preserve the provenance of the reviewed native genesis and verify
it with the pinned runtime during preflight. No secrets belong in any of these inputs.

Only fresh genesis is supported. A successor/migration state is rejected; use the separate
[succession](succession-approval.md), [retention](legacy-retention.md) and
[custody](validator-custody.md) requirements before adding a migration deployment path.

## Generate and inspect

```sh
checkedflow deployment-plan \
  --inventory /protected/inventory.json \
  --configuration /protected/operational.json \
  --genesis /protected/genesis.json \
  --destination /protected/review-001
```

The destination must be new and its parent must exist. Files are created exclusively, with private
POSIX modes; Windows operators must additionally enforce their directory ACL policy. Existing
plans are never overwritten. A failure leaves its partial directory for inspection. `plan.json` is
written last and binds the original input bytes and every generated file digest. Successful stdout
contains `REVIEW_REQUIRED`, the manifest digest, a file count and `hosts_changed: false`; it does
not print host addresses or local input paths. Input validation failure is a nonzero exit.

Each node receives the same reviewed application/genesis data and its own peer configuration:

| Generated file | Intended destination on that node |
|---|---|
| `operational.json`, `genesis.json` | `/etc/checkedflow/<name>/` |
| `config.toml` | `/var/lib/checkedflow/<name>/validator/config/config.toml` |
| `checkedflow-<name>-abci.service` | Operator-reviewed systemd unit directory |
| `checkedflow-<name>-validator.service` | Operator-reviewed systemd unit directory |

No private validator key, P2P key, signing-state file, default password or development certificate
is produced. Existing signing state must never be replaced by generation, copying another active
node, or resetting a directory. See [validator custody](validator-custody.md).

## Verify approved files and artifact bytes

Retain the reviewed plan's SHA-256 through a protected operator channel. Do not obtain the expected
hash from the untrusted bundle being checked. Supply the already built wheel and CometBFT binary:

```sh
checkedflow deployment-verify \
  --directory /protected/review-001 \
  --expected-plan <independently-approved-plan-sha256> \
  --wheel /protected/dist/checkedflow-0.2.0-py3-none-any.whl \
  --cometbft /protected/runtime/cometbft
```

Use the actual candidate filename; development artifacts still carry 0.1.0 metadata and must not
replace public 0.1.0. The verifier requires the wheel project/version to match the installed planner.
It verifies the approved manifest, rejects extra/missing files and links/junctions, regenerates the
expected files from the reviewed public configuration and compares every byte. It also checks
both supplied artifact digests. The reported file count includes the manifest and all 20 node files.
Reads are bounded: 128 KiB for the manifest, 4 MiB for a generated
file, 16 MiB for the wheel, 256 MiB for the binary and 64 KiB for wheel metadata.

The resulting JSON follows `checkedflow schema deployment-verification`. `BUNDLE_VERIFIED`
means the current file reads match the approved plan and artifact hashes. The result explicitly
retains `host_preflight: NOT_PERFORMED` and `hosts_changed: false`. It does not execute the binary,
prove its provenance or native version, verify a remote installation, establish key custody, test
kernel confinement or authorize service startup. A binary hash alone cannot prove that its contents
are a working CometBFT executable. Native host checks remain mandatory. The supplied artifact
paths are local verification inputs, not evidence about the executable paths on the four hosts.

Verification is an observation, not a lease on filesystem contents. Protected ownership must
prevent concurrent modification; revalidate while applying rather than trusting an old JSON result.
Original input hashes in the manifest preserve the approved provenance record; original source
files are not recovered or independently authenticated by reading that record. A partial or changed
bundle fails closed and is never repaired automatically.

## Inspect an already provisioned Linux host

Use `deployment-preflight` from the exact interpreter declared in the reviewed inventory:

```sh
sudo /opt/checkedflow/runtime/bin/python -I -m checkedflow.cli deployment-preflight \
  --directory /etc/checkedflow/review \
  --expected-plan INDEPENDENTLY_APPROVED_PLAN_SHA256 \
  --wheel /opt/checkedflow/checkedflow-0.1.0-py3-none-any.whl \
  --node node0
```

Paths above are placeholders for the approved inventory. Provision the runtime, accounts, all
21 review files, deployed configuration and units explicitly first. The command installs nothing,
does not start or stop services, and does not create, reset or copy signing state. Root access is
needed to inspect protected configuration; isolated Python prevents user environment settings
and the current directory from becoming import paths. Go must already be available at
`/usr/bin/go` for its native build-information inspector. It is an inspection prerequisite, not
an application runtime dependency.

The command verifies the independently approved bundle and artifact hashes, exact interpreter,
installed package bytes, protected path ancestry and service-account executable access. Runtime,
review and application-configuration paths must be root-owned and not group/world writable;
validator configuration ancestry may additionally belong to its non-root validator account.
Unexpected package source, changed configuration, alternate loaded unit paths, drop-ins, pending
daemon reloads and wrong service-account declarations fail closed. File modes are inspected, not
silently repaired. Protect ACLs, mount configuration and operator access separately; these mode
checks do not prove a complete host security policy.

Native version checking uses the fixed CometBFT main Go module version and checksum, rejects
module replacements, and checks the ABCI/block/P2P protocol versions. A display string alone
does not prove a build's origin: the upstream
[v0.40.0 source](https://raw.githubusercontent.com/cometbft/cometbft/v0.40.0/version/version.go)
retains a `0.39.0` display constant. The output preserves that reported value separately from
`cometbft_module_version`. Build metadata is checked against the approved binary hash; it is not
a substitute for an independently reviewed source/build provenance record.

`checkedflow schema deployment-preflight` describes the JSON result. `HOST_FILES_VERIFIED` is
a current observation with `hosts_changed: false` and `startup_authorized: false`. Its remaining
checks explicitly include network confinement, validator custody, candidate isolation and live
quorum/recovery. No service lifecycle action may treat this result alone as permission to start.
Readiness can change after the observation; repeat inspection at use.

The [four-VM preflight observation](evidence/deployment-preflight-20260927.json) records actual
installed CLI results and retained failed attempts. It identifies the tested wheel and explicitly
separates a later unit-tested ancestry guard from that installed result. It is component evidence,
not final-artifact release qualification.

## Service and network requirements

### Explicit local start, status and stop

After independently reviewing the plan and provisioning the host, use the reviewed interpreter:

```sh
sudo /opt/checkedflow/runtime/bin/python -I -m checkedflow.cli deployment-service \
  --directory /etc/checkedflow/review --expected-plan APPROVED_PLAN_SHA256 \
  --node node0 --action status

sudo /opt/checkedflow/runtime/bin/python -I -m checkedflow.cli deployment-service \
  --directory /etc/checkedflow/review --expected-plan APPROVED_PLAN_SHA256 \
  --node node0 --action start --wheel /opt/checkedflow/checkedflow-0.1.0-py3-none-any.whl

sudo /opt/checkedflow/runtime/bin/python -I -m checkedflow.cli deployment-service \
  --directory /etc/checkedflow/review --expected-plan APPROVED_PLAN_SHA256 \
  --node node0 --action stop
```

The operator's explicit `start` invocation requests the action. A stored preflight result never
authorizes it. Before invoking it, complete the independent custody and confinement checks in this
guide. The command repeats current preflight checks, derives the public identities from existing
local native keys, compares them to the reviewed validator/P2P identities, and requires existing
native signing state. It neither generates nor exports private keys and never resets signing state.
The systemd validator retains its exclusive custody lock while running.

`START_REQUESTED` means systemd accepted a nonblocking start request, including the required ABCI
service. It does not mean the process is healthy, caught up or participating in quorum. The returned
service observation precedes the request; call `status` and verify actual committed progress and
common-height hashes separately. Every result keeps consensus `NOT_CHECKED` with a reason.

`stop` requests normal systemd shutdown of the validator and ABCI units. It observes inactive units,
zero main PIDs and empty or removed cgroup-v2 groups, then verifies the retained key identities and
signing-state file before returning `STOPPED`. It does not force-kill a stuck process. The observation
loop has a 90-second deadline; each native inspection additionally has a 15-second timeout. A lost
request reply, stuck process, populated group or unverified signing state returns `OUTCOME_UNKNOWN`.
Inspect current status and preserve the original home; do not delete a lock or reinitialize to retry.

Stopping does not require a wheel or execute a potentially damaged CometBFT binary. It still requires
the approved plan, selected node's local configuration and matching loaded unit definitions.
A wrong node selection is rejected before requesting a stop. A changed or foreign service definition is
not an approved automated stop target; use the operator's separate incident containment procedure.
These commands operate only the named local ABCI/validator services. They do not drain mission work,
stop independent workers, prove that no external signer copy exists, or authorize key relocation.
Use governed mission draining before planned work-service maintenance and the separate
[custody procedure](validator-custody.md) before transferring or replacing a validator.

`checkedflow schema deployment-service` is the machine-readable observation contract. Status is
read-only; start/stop serialize through a persistent root-owned deployment lock. These service
commands do not install units, enable boot startup,
changes firewall rules, rolls back a database, or performs automatic artifact repair.

An [installed four-VM lifecycle run](evidence/deployment-service-20260927.json) exercised these
commands: start all four nodes, observe services, stop one node while the other three advance,
restart it, compare common-height hashes, then stop all nodes and preserve signing state. Every
CLI result was checked against the packaged schema. The evidence binds the exact wheel and runtime
source files; it does not qualify work draining, cross-host key relocation or the complete release.

The units use separate non-root accounts `cf-<name>-app` and `cf-<name>-val`, a read-only system
view, explicit writable directories and a restrictive umask. The validator runs under its persistent
custody lock, refuses a conflicting local owner and does not force-kill a slow shutdown. Provision
accounts, directory ownership and access to public configuration/runtime files explicitly before use.
The validator requires the application service and pre-existing key/signing-state/P2P-key files.

RPC and ABCI bind only to IPv4 loopback. P2P binds to the declared private address, configures only
the other three persistent node IDs and disables peer exchange and dynamic outbound discovery.
ABCI uses the gRPC `passthrough:///127.0.0.1:26658` resolver target. The socket transport's
`tcp://` target must not be substituted: the native gRPC client can remain waiting for its echo.
The mempool transaction ceiling is 1 MiB, matching CheckedFlow's wire contract. The RPC request
body ceiling is 2 MiB so the base64-encoded transaction and JSON envelope fit at that boundary.
The unit additionally declares a default-deny IP policy with only loopback and the four inventory
addresses allowed. Verify actual systemd/cgroup-BPF enforcement and network restrictions on every
host before enabling services: a parsed directive or a private address is not evidence of enforcement.
Do not expose management endpoints through a public listener to bypass that verification. Remote
agent services need the separately configured [authenticated TLS](agent-tls.md) path.

Configuration keys follow the pinned
[CometBFT 0.40.0 template](https://raw.githubusercontent.com/cometbft/cometbft/v0.40.0/config/toml.go).
Runtime hashes, image provenance, application installation, key custody, service confinement,
peer reachability and recovery must all pass actual host preflight and bounded deployment tests.
This command does not perform that preflight. Do not treat this plan or its manifest as release
approval or as an executable authorization for an agent to change hosts.

## Verification status

Source tests cover deterministic output, cross-input genesis binding, purpose-separated public
identities, no generated signer material, duplicate/private-peer validation, injection/traversal
rejection, byte limits and refusal to overwrite a plan. TOML syntax is parsed by the standard library.
Installed-artifact checks cover the offline CLI. A [four-VM native experiment](evidence/deployment-native-20260927.json)
also applied the exact generated service/configuration bytes, reached a common committed block's
application hash, compared all 196 package members on every host, and stopped the services and VMs
while preserving signing state. Earlier failed attempts remain recorded. In particular, runtime
executables must be accessible to the non-root service accounts; copying private file modes is not
installation. The successful run used the corrected gRPC resolver target above.

This experiment used private orchestration on one physical host and one operator. Full work-service
deployment recovery, adversarial kernel enforcement checks and complete G6 remain unfinished.
No measured hardware minimum or deployment-time promise is asserted.

## Apply reviewed public files

Use `deployment-apply` after explicitly provisioning the reviewed runtime, separate non-root service
accounts, owned application/validator homes, native keys, signing state and persistent custody lock.
It does not provision these prerequisites. The supported local filesystems are ext4 (reported by
`stat` as `ext2/ext3`), XFS and Btrfs. Remote filesystems and temporary memory filesystems are rejected.

```sh
sudo /opt/checkedflow/runtime/bin/python -I -m checkedflow.cli deployment-apply \
  --directory /etc/checkedflow/review --expected-plan APPROVED_PLAN_SHA256 \
  --node node0 --wheel /opt/checkedflow/checkedflow-0.1.0-py3-none-any.whl
```

The command authenticates the reviewed bundle and installed runtime, verifies account separation
and local key identities, and requires stopped services with no pending jobs or remaining processes.
It holds both the root-owned deployment lock shared with start/stop and the validator custody lock.
Direct administrator actions outside these commands still require operator coordination.

It checks all five destinations for conflicts before installing any file. Existing identical files
are retained; different files are rejected. New public files use anchored directory descriptors,
exclusive staging and non-replacing links, followed by file and directory synchronization. The
command reloads systemd and repeats preflight, but never enables or starts services. Read the
`deployment-apply` schema: `FILES_APPLIED` and `startup_authorized: false` mean verified file
installation, not deployment readiness. First application reports five created files; an unchanged
repeat reports zero. Account permissions and selected-node binding are checked again at startup.

This is not an all-files transaction or an upgrade mechanism. A failure can leave a subset of new
files or a staging file. Preserve them, inspect the exact destinations and retry the same approved
plan after resolving the reported cause. A lost reload/readback result is `OUTCOME_UNKNOWN`;
never remove validator state or replace conflicting files to manufacture success.

The [installed four-VM apply experiment](evidence/deployment-apply-20260928.json) passed new-file
application and unchanged repetition on every node, followed by startup, three-node progress during
one-node interruption, recovery to a common state hash, and confirmed shutdown. Native Linux source
tests also exercise non-overwrite, symlink-parent rejection, FIFO rejection without blocking and
exclusive locking. This evidence remains component-scoped and predates final release artifacts.
