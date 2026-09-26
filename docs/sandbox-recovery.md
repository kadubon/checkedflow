# Independent sandbox recovery

A worker's cleanup block cannot run after `SIGKILL`. The draft 0.2 runner therefore requires a
separate recovery service before it can execute any candidate. Both the legacy demonstration and
the v2 repository worker use this runner. This is an operational configuration change in the draft;
it changes no signed consensus bytes and does not imply a qualified rolling upgrade.

## Start safely

Use one private recovery directory and one service per local Docker daemon, shared by that host's
workers. Docker is accessed explicitly through `unix:///var/run/docker.sock`; remote contexts,
`DOCKER_HOST` overrides and alternate sockets are not supported by this implementation. The kernel
boot identity and monotonic deadlines belong to the same machine as the containers.

For a disposable Linux laboratory, start this command in a separate terminal before the worker:

```sh
export CHECKEDFLOW_SANDBOX_RECOVERY="$HOME/.local/state/checkedflow/sandbox"
uv run python -m checkedflow.sandbox_recovery "$CHECKEDFLOW_SANDBOX_RECOVERY"
```

Set the same variable in the worker's terminal. Keep the service running until all work and cleanup
have settled. There is no host-execution fallback. A missing journal setting, stale recovery heartbeat,
unavailable daemon, changed daemon identity or full recovery journal prevents new container launch.
`python -m checkedflow.sandbox_recovery DIRECTORY --once` performs one recovery pass; it does not
advertise a continuing service. All commands are available from an installed wheel without a checkout.

For an operated deployment, supervise recovery in its own service, independently of the worker's
process group and stop/restart policy. The packaged
[systemd template](../src/checkedflow/data/sandbox-recovery.service) documents the intended separation.
Review its user, paths and Docker permissions before installation; the package neither creates an
operator account nor installs/enables the service. Docker access itself is host-administrative trust.
No generated code receives that access, the recovery journal, or a writable host mount.

## Failure windows and ownership

The runner uses Docker's distinct [create](https://docs.docker.com/reference/cli/docker/container/create/)
and [start](https://docs.docker.com/reference/cli/docker/container/start/) operations. Creation alone
does not execute the configured program.

| Step | Durable state and recovery action |
|---|---|
| Before creation | Commit a random name, ownership label, boot identity and deadline to SQLite. |
| Creation reply is missing | Keep the name and label pinned. A successful absence query cannot rule out a delayed creation. |
| Before start | Verify the stopped container's name, label, gVisor runtime and immutable ID; commit that ID. Start only that ID. |
| Worker dies before recording the creation reply | The service can discover the labeled container by its recorded name and remove it when due. |
| Worker dies while code runs | The independent service removes the recorded container at its deadline. |
| Removal reply is lost | Preserve the immutable ID. A later successful absence query can establish removal of that particular container. |
| Daemon is unavailable or ownership differs | Preserve the record, report failure and stop advertising fresh readiness; never remove an unverified container. |

An inventory of labeled containers must match the journal before the service advertises readiness.
A missing journal cannot silently ignore existing managed containers. Ownership checks use an exact
name, random label, gVisor runtime and, once known, immutable ID. Removal uses the immutable ID,
so a delayed start cannot recreate a removed container or address a replacement with the same name.
The label is an ownership marker inside the trusted Docker administration boundary, not a secret or
a defense against a malicious host administrator.

The journal has at most 32 unresolved creation intents. Unseen creation outcomes remain pinned and
can exhaust that capacity. Do not clear the database, invent a new directory or delete records to
recover capacity. Keep the daemon, journal and any pending creation history for operator reconciliation.
There is intentionally no automatic absence-based retirement of an unseen creation request.

## Deadlines and limits

The execution deadline starts before Docker creation, and creation/inspection overhead consumes it.
It is a local monotonic limit, not a change to consensus block-height leases. A new boot makes all
old journal entries due for recovery. Before issuing or binding an intent, the worker requires a
successful service sweep observed within three seconds. The service normally polls every 250 ms;
Docker calls, scheduling and restart delay add latency. These intervals are configuration choices,
not a measured hard real-time termination guarantee.

The existing gVisor, nonroot, network-disabled, read-only, memory, CPU, process and output bounds
remain in place. Container restart policies and image health checks are disabled. Docker's persistent
log driver is disabled; bounded output is collected through the attached process streams.

If both worker and recovery service stop, cleanup requires restarting the recovery service. If
Docker or the host is unavailable, process termination cannot be proven. An unknown result remains
unknown even after the container is removed; recovery does not authorize another execution.

## Evidence and remaining limits

Source tests cover lost create/remove replies, a delayed creation, expired deadlines, reboot,
ownership mismatch, daemon failure/change, concurrent cleanup, capacity, readiness and durable
create-before-start ordering. The real installed-wheel qualification adds hard worker exits before
binding a created container and after observing a running gVisor container; a separate process must
remove it. Only a successful exact-artifact run qualifies those cases.

The systemd template is not evidence of an operated four-host recovery service. Service-manager
restart fault tests, coordinated journal/daemon disaster recovery, and collection of temporary
workspace directories left by hard-killed workers remain unqualified. This mechanism recovers OCI
containers; it does not delete arbitrary host directories, restore validator keys, reconcile GitHub
effects or complete the G1–G7 release gates.
