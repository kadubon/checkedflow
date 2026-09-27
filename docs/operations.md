# Linux operation and recovery

The reference deployment has four independently managed organizations, equal CometBFT voting
power and fixed membership. Each organization runs its own full node, ABCI application, worker,
protected keys and local store. Workers query only their organization's validated node.
All four organizations can read the tasks, source and evidence.

Agent-facing gateways are separate operator processes. A gateway binds one existing mission and
queries that organization's own node; it does not replace workers or consensus. Start `checkedflow
a2a` or `checkedflow mcp` only after bootstrap. Follow [agent communication](interoperability.md)
for bearer provisioning, stdio host configuration and remote tunnel boundaries. Use separate
tokens/processes for separate mission visibility. Restore the private A2A journal on restart
to retain observation ordering, page tokens and callback configuration; consensus tasks remain
in the own-node store. Back up SQLite consistently, including WAL state or using its backup API.
After `OUTCOME_UNKNOWN`, inspect committed task/receipt state before authorizing another attempt.
The laboratory's commit-notification wait is 20 seconds, below the RPC client's 30-second
timeout. This only bounds response waiting; it cannot guarantee finality. A notification timeout
may follow a committed command and is always reported as `OUTCOME_UNKNOWN`, without resubmission.

For transport diagnosis: `401` means missing/invalid admission credentials, `403` can mean a
rejected browser Origin or OAuth scope, `CURSOR` requires restarting a changed listing, and
`OUTCOME_UNKNOWN` requires own-node inspection. A callback that fails three times remains
configured but stops delivery until reconfigured. A disconnected stream does not cancel work.
Inspect [security boundaries](security.md) before exposing a gateway through a TLS proxy.

## Provision the external runtime

CometBFT and gVisor are external dependencies, never Python package dependencies. Install a
verified CometBFT **v0.40.0** build. The source module is available from the
[Go module proxy](https://proxy.golang.org/github.com/cometbft/cometbft/@v/v0.40.0.info).
Its Go module checksum is `h1:0+zc7FlcnonFfMwzhyDCd5vK/fHZqz0RfRyTCxZsFPU=`.

```sh
GOBIN="$PWD/tools" go install github.com/cometbft/cometbft/cmd/cometbft@v0.40.0
go version -m tools/cometbft
```

The published module retains a `0.39.0` fallback CLI version constant. CheckedFlow's laboratory
checks embedded Go module identity and checksum when that display differs. Do not accept an
arbitrary 0.39 binary. Retain your binary hash and build provenance.

Follow [gVisor installation](https://gvisor.dev/docs/user_guide/install/) and verify the release
archive checksum. Current archives contain `runsc` **and adjacent `gvisor-bin` sidecars**; keep
them together. Register `runsc` with your Linux Docker daemon. The qualified runtime version
and image digest are recorded in [validation status](validation-status.md) and the
[machine-readable runtime lock](../deploy/runtime-lock.json).

```json
{"runtimes":{"runsc":{"path":"/opt/gvisor/runsc"}}}
```

Merge this operator configuration with existing daemon configuration; do not blindly replace an
existing file. The draft runner explicitly uses the local `/var/run/docker.sock` and requires the
[independent sandbox recovery service](sandbox-recovery.md) with one private journal per daemon.
Alternate sockets and remote Docker contexts are not supported by this recovery profile.
The worker requires access to the daemon socket; the generated container never receives it. See
[gVisor's security boundary](https://gvisor.dev/docs/architecture_guide/security/) for host and
kernel assumptions. Isolation does not remove the operator from the trusted base.

Pull a Python image during provisioning, inspect its content digest and verify a minimal run:

```sh
docker pull python:3.12-slim
docker image inspect python:3.12-slim --format '{{index .RepoDigests 0}}'
docker info --format '{{json .Runtimes}}'
```

Supply the resulting `python@sha256:...` to mission and verifier registration. Pin the same
checker contract and image across organizations. The runner uses `--pull=never`.

## Configure nodes

Provision a common CometBFT genesis with four distinct Ed25519 validators of equal positive
power. Its `chain_id` must equal the application genesis chain. `app_state` must be the exact
portable CheckedFlow genesis. Initial height is 1. Keep application vote extensions disabled,
ordinary flood mempool, state sync disabled and full block retention.

For CometBFT 0.40's gRPC client, use an explicit passthrough resolver:

```toml
proxy_app = "passthrough:///127.0.0.1:26658"
abci = "grpc"
```

Expose the P2P listener only to the intended validator network. Bind RPC and ABCI to loopback.
Set persistent peers to the other three validator identities. Set block maximum bytes to
2,097,152 and mempool maximum transaction bytes to 1,048,576 to fit the application limits.
Set `[rpc].max_body_bytes = 2097152` as well: base64-encoded transactions exceed their raw size.
The laboratory config generator is in [cluster.py](../src/checkedflow/distributed/cluster.py).
Its duplicate-IP relaxation and colocated keys are for single-host testing only.

Start the application before CometBFT. Obtain three organization signatures on worker,
verifier and mission registrations. Each worker invocation performs at most one ready task or
resumes one completed generation's candidate proposal:

```sh
checkedflow worker --rpc http://127.0.0.1:26657 --identity worker-a \
  --key /private/worker-a.key --chain my-chain
```

The minimal worker is intentionally a single-attempt process; an operator supervisor schedules
invocations. Different worker processes must not share a signing identity concurrently. They
may compete for the same task with different identities; consensus admits only one lease.

## Worker recovery boundaries

| Interruption | Committed record | Recovery action |
|---|---|---|
| Before lease | Ready task | Another authorized invocation may acquire it. |
| After lease, before start | Leased task | Bundled worker leaves it to expiry and explicit reconciliation. |
| After start, before result | Running task | Never rerun automatically; expiry records uncertainty and charges the reservation. |
| After generation receipt, before proposal | Finished task with source and digest | Restart the same worker identity; it proposes the stored source without generation. |
| After proposal, before RPC reply | Capability already associated with the task | Query committed state; restart will not propose a second artifact for that task. |
| During verification | Running organization-scoped task | Preserve its unknown outcome; reconcile before a fresh attempt. |

The current worker does not heartbeat while its sandbox runs. Choose a lease long enough for
the declared execution limit and normal commit latency. A custom supervisor may send authorized
heartbeats, but it must preserve fencing and cannot extend beyond mission expiry. If quorum stops,
block-height time stops; local sandbox wall-time limits still apply.

## Diagnose a refused run

| Observation | Inspect next |
|---|---|
| `SANDBOX_UNAVAILABLE` | Linux host, daemon socket permissions and `docker info` runtime configuration. |
| `exit_nonzero:125` | Docker stderr and local image presence; `--pull=never` deliberately refuses a missing image. |
| `BUDGET` on lease/proposal | Spent/reserved amounts, protected floor and all four required verification slots. |
| `FENCE`, `LEASE` or `NONCE` | Committed owner, deadline and current actor nonce; check for concurrent use of one key. |
| `DEPENDENCY` | Source capability status and expiry, including all ancestors. |
| No block progress | At least three validators connected, application logs, disk state and CometBFT peer configuration. |

Keep stdout/stderr and node logs local for diagnosis. An absent sandbox never authorizes host
execution. Provision missing images using the approved digest before rerunning qualification.

## Recovery

- Restart an application against its original database and genesis. CometBFT reconciles the
  committed height and replays necessary finalized blocks. A pending in-memory block is discarded.
- If the application store is lost, stop that node, preserve CometBFT consensus data and keys,
  provision a fresh application store with the same genesis, then replay the retained block log.
- If a snapshot hash differs from its stored bytes, investigate storage corruption and reconstruct
  from the consensus log. Never trust an unverified remote snapshot as repair authority.
- With fewer than three validators communicating, stop new dispatch. There is no quorum bypass.
- An expired or interrupted attempt is `uncertain`. Reconcile the external outcome before a
  three-signature `task.reconcile` command authorizes retry. Fencing must also exist at any real
  external actuator; a runtime token alone cannot prevent a late physical effect.
- Revoke compromised worker keys with three organization signatures. Dependent evidence is
  quarantined; repair through fresh checked artifacts with explicit residual references.

Back up keys separately from data, retain consensus history and test restoration. Do not clone
a validator signing state into two active nodes. A complete governance-key rotation or membership
change requires a new protocol/deployment; it is not implemented by v1.

Inherited v2 genesis requires [succession startup admission](succession-approval.md#startup-admission)
on every ABCI service start. Retain the independently provisioned old checkpoint, full snapshot
and both administrations' approval manifest with the protected initial configuration. This check
is separate from old-dispatch shutdown and exclusive validator custody.


The v2 own-node client reads synchronized status before querying application state. If the
application response is below the height just observed, it makes exactly one additional state
query. Both reads must use the configured validating node; the second must reach the original
height or the operation fails with `STALE`. No cached state, lower height, write retry, or extra
status poll is substituted. This bounds a live-state call to one status and at most two state
queries, each using the configured RPC timeout. Persistent lag remains an availability failure.
