# v2 CometBFT adapter and local laboratory

Status: a separate v2 ABCI service and client, with source tests and a mandatory live
qualification case. Read the [implementation record](implementation-0.2.0.md) for observed
results. This is not yet the complete four-host operational profile or an artifact-adoption service.

## Protocol separation and genesis

The v1 ABCI service remains unchanged. The new service is
`checkedflow.distributed.operational_application`, with ABCI application protocol version 2.
Its state and signed-command formats are v2; package version and protocol version are separate.
Development builds still carry unpublished development changes and must not replace published 0.1.0.

Each operator pins the same application configuration and CometBFT genesis before startup.
The [configuration schema](../src/checkedflow/data/operational-configuration.schema.json)
contains a fresh v2 state and four ordered organization-to-validator-public-key mappings.
The schema embeds the structural state contract so it requires no network schema resolution.
Runtime checks additionally require:

- Four distinct validator keys, exactly matching the initial organization mapping.
- Validator keys distinct from every application command key, including administrators.
- Four equal, positive voting powers in CometBFT's `InitChain` request.
- Exact chain identity, initial height 0 or 1, and matching application genesis contents.
- A fresh application store: height and nonces zero, paused mode, no funding or tasks.

Changing a filename or connecting to another RPC server is not a migration. This adapter does
not reinterpret v1 storage, create a new genesis from an active snapshot or rotate validators.
The initial configuration is trusted operator input; it is not obtained from an arbitrary remote API.

## Finalization, commit and recovery

`CheckTx`, `PrepareProposal` and `ProcessProposal` evaluate original signed transaction bytes
against transient copies of committed state. Proposal selection is bounded to 256 inspected
transactions and the smaller of CometBFT's byte allowance and the application's 2 MiB transaction
budget. Each transaction is at most 1 MiB. Invalid proposals reject without changing durable state.

`FinalizeBlock` requires the next contiguous height, computes deterministic outcomes and keeps
them transient. A conflicting second finalization at that height is rejected. Queries continue to
return committed state. No sandbox, callback, artifact fetch or external effect runs in this path.

At `Commit`, the v2 store replays the exact finalized bytes inside its atomic SQLite transaction.
The adapter checks that the durable state hash and transaction outcomes match finalization before
acknowledging and exposing the new state. Storage failure does not publish the pending state.
A restarted application reports its durable height and app hash to CometBFT for normal replay.
Empty blocks also commit deterministic task expiry and conservative funding settlement.

Consensus agreement does not make a worker observation true. The adapter orders authenticated
commands; separate evidence quorum, acceptance and reuse rules still need integration.

## Own-node client and query scope

`checkedflow.distributed.operational_client.Client` accepts an explicitly named chain and a
literal loopback HTTP endpoint controlled by the operator. An explicit SSH tunnel can preserve
that own-node relationship; an arbitrary remote server is not a validating node substitute.

The client sends the original signed bytes as base64 and queries `/v2/state`. It checks chain and
reported height against the decoded state. `/v2/hash` exposes the committed application hash.
Historical/proof queries and unrelated paths reject. This is a local operator interface, not a
public agent API or an authenticated remote artifact service.

Responses are read with an 8 MiB ceiling, redirects and ambient proxies disabled, and compressed
responses rejected. I/O timeout configuration must be positive and at most 60 seconds. This is not
an end-to-end dispatcher freshness guarantee: supervision must still inhibit new execution when
own-node state is stale or quorum stops advancing.

A committed transaction receipt is not task completion. A timeout, transport error or malformed
commit reply is `OUTCOME_UNKNOWN`; callers must inspect the committed logical task and nonce.
Do not replace the operation with a new task or blindly resend an external effect. A competing
lease can time out in the mempool even when another worker's lease has already committed.

## Running the separate service

Install the distributed extra from the qualified distribution, prepare the reviewed configuration,
then run the loopback service beside that operator's CometBFT process:

```sh
python -m checkedflow.distributed.operational_application \
  --configuration operational.json \
  --database node/operational.sqlite \
  --address 127.0.0.1:29652
```

CometBFT 0.40.0 must use gRPC ABCI and the matching application configuration in `app_state`.
The directory and database must be private, operator-controlled inputs. Configure the CometBFT
process with its own validator key; never supply a command-signing key as that validator key.
The service does not open a public port, manage a daemon or provision independent operators.

For a disposable local laboratory, `distributed.operational_cluster.Cluster` owns a new directory
and the processes it starts. It reuses the pinned CometBFT process/configuration preparation,
creates separate validator and command identities before startup, and supplies v2 services.
Each node has its own SQLite store and validator signing state. The laboratory's creator owns
all four organizations' test keys; it does not establish organizational independence.

```python
from pathlib import Path
from checkedflow.distributed.operational_cluster import Cluster

lab = Cluster(Path("v2-laboratory"), "cometbft")
try:
    lab.start()
    lab.send("budget.configure", {"budget": 100, "verification_reserve": 30})
    lab.send("mission.resume", {})
    print(lab.client().state().mode)
finally:
    lab.close()
```

The laboratory helper is synchronous and supports its initial key revisions. It is not the
persistent multi-identity worker supervisor. Applications must not parallelize command submission
under a shared signing identity; the competing-lease test uses distinct worker identities.

## Qualification boundaries

The mandatory live case uses the installed wheel and real CometBFT/gVisor. It approves funding
and a repository target, races two distinct workers for ownership, starts the committed attempt,
checks the licensed invoice fixture in gVisor, saves exact observation bytes in scoped local
artifact storage and commits the result digest. It also exercises one-node loss, quorum loss,
crash/restart recovery, common application hashes and independent local history replay.
Mempool ambiguity is reconciled against committed ownership rather than counted as rejection.

That case is a manually driven SDK workflow. It does not establish three-organization artifact
acceptance, supervised worker freshness, artifact availability receipts, S3 support, conditional
reuse, authenticated snapshots, four-host operation or safe live GitHub effects. ABCI snapshot
offers currently reject and no snapshots are advertised; trusted recovery/bootstrap remains
separate required work. A source/mock test or skipped infrastructure case cannot qualify release.
