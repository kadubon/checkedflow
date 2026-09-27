# Reviewable four-node deployment plans

`checkedflow deployment-plan` generates configuration and service files for four pre-provisioned
Linux nodes. It does not connect to hosts, create accounts or keys, install software, start services,
change a firewall, or move validator signing state. This is the offline planning part of the operational
deployment path. Managed apply/start/status/drain/stop/restore and full multi-host qualification
remain unfinished; generated files are not evidence of a deployed or qualified service.

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

## Service and network requirements

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
Installed-artifact checks cover the offline CLI. Native runtime, kernel enforcement and four-host
service application tests remain required. No measured
hardware minimum or deployment-time promise is asserted.
