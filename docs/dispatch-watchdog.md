# Dispatch freshness watchdog

Status: development component for 0.2.0, not a complete worker supervisor. A responsive node can
be unable to commit new blocks. `Watchdog` therefore requires both recent successful reads and
observed committed-height progress before returning a locally usable observation. It never
grants task or GitHub authority.

## Reader and policy

`checkedflow.distributed.operational_client.Client.live_state()` first reads CometBFT status.
It requires the configured chain, the exact boolean `catching_up: false`, and a positive canonical
height. It then obtains and validates the v2 application state through the existing bounded
own-node transport. The application height must not precede the observed node height. The two
queries are not atomic: a transient height mismatch inhibits dispatch and requires new reads.
The implementation follows the pinned
[CometBFT 0.40.0 status source](https://github.com/cometbft/cometbft/blob/v0.40.0/rpc/core/status.go)
and [response types](https://github.com/cometbft/cometbft/blob/v0.40.0/rpc/core/types/responses.go).

Use only an operator-controlled validating node. The sync flag is a local node observation, not
proof of current quorum, an independently verified block certificate or a trust bootstrap.
A malicious node or privileged process can defeat this local policy.

```python
from checkedflow.dispatch_watchdog import Watchdog
from checkedflow.distributed.operational_client import Client

client = Client("http://127.0.0.1:26657", chain="approved-chain", timeout=2)
watchdog = Watchdog(
    client.live_state,
    chain="approved-chain",
    mission="approved-mission",
    max_read_age_ns=5_000_000_000,
    max_stall_ns=10_000_000_000,
)
observation = watchdog.poll()  # Read-only network access; startup is still inhibited.
# The owning supervisor schedules subsequent polls within its finite resource envelope.
# current() succeeds only after another running observation with a greater committed height.
```

These example durations are policy inputs, not measured availability objectives. Both are explicit
positive integer nanoseconds, at most one hour. The clock defaults to Python's process-local
monotonic clock. It is outside consensus, is never serialized into a command, and cannot extend a
block-based lease. A port needs a nondecreasing local monotonic clock; do not persist or compare
these clock readings across processes or hosts. Platforms differ in whether suspend time is counted;
operators must stop and cold-start the supervisor after host suspension or ambiguous clock continuity.

## Inhibition rules

| Condition | Result |
|---|---|
| Startup, restart, prior failed read, stale sample or stalled progress | Require two successful running samples with strictly increasing height |
| Same-height identical read | May refresh read age; cannot refresh progress age |
| Read age or progress age reaches its limit | Clear readiness, even when no further poll is attempted |
| Read takes as long as the read-age limit | Reject it as `STALE`; elapsed network time does not create a fresh sample |
| Paused or draining mission | Clear readiness; later running samples must warm up again |
| Concurrent read attempt | Reject with `BUSY`; one reader is in flight |
| Read in flight | `current()` denies readiness; `stop()` remains available without waiting for the network |
| Decreasing height or different state at the same height | Latch `STOPPED` after a `CONFLICT`; retain the high-water observation |
| Invalid or decreasing clock | Latch `STOPPED` after `CLOCK` |
| Local `stop()` | Permanently inhibit this instance, including a late successful read |

Age starts when the request starts, not when its response arrives. Read failures invalidate the
observation sequence before propagating to the caller. Scope/profile mismatch does not erase the
retained high-water state. A restart starts cold but does not provide durable rollback protection;
the [worker coordinator](worker-supervision.md) separately persists observation floors and excludes
local concurrent senders. Protected checkpoint recovery and cross-host ownership remain requirements.

`poll()` returns an observation even when cold or paused. It must never be used as a dispatch
permission. Call `current()` immediately before an independently authorized operation, then verify
the exact task owner, fence, funding, current acceptance, destination and effect policy. A returned
Python state is not a permit that can be stored and reused. A later concurrent stop cannot revoke
an already returned value or cancel a provider request. Unknown in-flight effects still require
durable reconciliation, not automatic resubmission.

No background thread, poll scheduler, provider call, clock-driven consensus transition, automatic
resume, process kill or metric exporter is installed. `poll()` performs the supplied read; `current()`
and `stop()` perform no network or filesystem I/O. The existing HTTP timeout bounds individual I/O,
not a total process deadline. An outer supervised process must enforce its total resource envelope.

## Recovery and validation

On `NOT_READY`, stop starting protected work and continue bounded read-only observation. On
`CONFLICT`, preserve logs and diagnose node/storage rollback before replacing the watchdog. Do not
restart repeatedly to hide the conflict. After an emergency stop, first check current policy and
unresolved effects; a new watchdog only reestablishes local freshness, not permission to resume.

Source tests cover clock/height regression, same-height inconsistency, repeated successful stale
reads, slow reads, pause/drain, lost connections, cold restart and concurrent emergency stop.
Mutations remove read expiry and let unchanged height renew progress; both must be detected.
The installed-package smoke checks default inhibition outside the checkout. The real single-host
CometBFT/gVisor case adds warmup, two-node quorum-loss inhibition despite successful queries, and
post-recovery progress. The actual run result belongs in the implementation ledger; registering a
test is not evidence that it passed. Four-host and full supervised-effect qualification remain open.
