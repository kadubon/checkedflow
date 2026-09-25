# Concepts and guarantees

## The problem CheckedFlow addresses

Generating another candidate can be cheaper than deciding whether an existing one is usable.
A runtime that records only successful outputs loses the cost of checking, uncertain attempts
and the conditions under which an older result remains valid. CheckedFlow makes those conditions
part of state, alongside work ownership and budget reservations.

Its unit of reuse is a **capability record**: exact source bytes, their digest, the originating
task, dependency identities, a lifetime, and signed observations. A capability's `checked` status
means the configured acceptance rule has been satisfied. It is always scoped to a mission's
contract, receiver, verifier and image. The receiver is a declared usage label, not an automatic
proof that a real downstream system is compatible.

## Who participates

Four organizations approve the initial membership and have equal CometBFT voting power.
Each operates a validator/full node, an ABCI application and workers with separate keys.
Three organization signatures are needed to register workers, define verifiers, approve missions
or reconcile uncertain work. A worker may have one or more of these roles:

| Role | Authorized work |
|---|---|
| Producer | Create mission tasks and propose source from its completed generation task. |
| Executor | Acquire and run generation, execution or repair tasks. |
| Verifier | Run the verification task assigned to its organization and sign its observation. |

One worker identity may combine roles, but three votes from that identity or organization do not
count as three independent organizations. Independence of actual operators is a deployment
assumption. The single-host laboratory uses four keys to test the mechanics.

An **agent** is a client of this system, not an additional privileged role. It may use A2A to
communicate with a service or MCP to call tools exposed by a local process. A **gateway** translates
those requests into the existing signed-command boundary for one mission. A bearer credential or
access to an MCP tool grants transport visibility; only registered signatures establish authority
to change state. The gateway does not generate approval signatures or execute received code.
See [agent communication](interoperability.md) for a complete request-to-receipt example.

## Four records that answer different questions

| Record | Question answered | What it does not establish |
|---|---|---|
| Signed command | Which registered identity requested this change? | That its claim is true. |
| Mission and committed lease | Who may perform this task, at which fence and maximum cost? | That execution succeeded. |
| Verification receipt | What did this checker report for these source bytes and contract? | Correctness beyond that checker and declared domain. |
| Committed state | In which order did the organizations agree to apply commands? | The truth of observations outside consensus. |

The worker reads its own validated node before dispatch. Submitted code never executes inside
the agreement process and receives no signing keys. A gVisor runtime name in Docker configuration
is an operator-provided trust assertion; CheckedFlow does not remotely attest the host binary.

## When reuse is permitted

Every declared dependency and its ancestors must still be `checked` and unexpired. The source
and destination missions must agree on contract, receiver, verifier identity and image digest.
Capability IDs are immutable, so an ID identifies one version of the source and dependency list.
Changing a checker requires a newly approved definition and mission.

Three matching passing observations admit a candidate. A negative observation or contradictory
behavioral digest quarantines it, even after three passes. This conservative policy preserves
dissent and stops further reuse. It allows one malicious verifier to deny artifact availability;
CometBFT's one-fault consensus assumption is a separate claim about ordering.

Revocation and expiry propagate to descendants. Quarantine is not reversed in place. Rebuild
under eligible dependencies and obtain fresh checks. When withdrawn evidence had resolved an
older residual, that residual reopens while retaining its original reason and resolution link.

## Unknown is a retained state

An expired lease, unavailable sandbox or interrupted attempt may leave no trustworthy result.
The task becomes `uncertain`; its reserved upper-bound cost is charged, and a residual retains
the reason and recheck trigger. An administrative quorum may authorize retry or abandonment.
The original obligation remains visible. A retry increments the fence and requires fresh funding.

The bundled worker refuses external side effects. A custom actuator would have to enforce fences
where the effect happens and reconcile ambiguous outcomes. A runtime lease cannot undo an action
already performed in another system.

## What the demonstration measures

For `integer-arrays/v1`, behavior identity is a hash of outputs over all 31 declared inputs.
Two source files that behave identically there count as one live generated behavior in a mission.
This is not a general novelty test. External origin is an explicit producer declaration, not an
independently detected property of source code. An unknown novelty judgment stays unknown.

The reuse/scratch experiment reports grammar candidates examined under matched declared limits.
It separately reports upper-bound work units charged for generation and verification. Neither
quantity is a measurement of general intelligence, economic return or causal acceleration.
The [state-machine reference](state-machine.md) specifies the counters precisely.

## Protocol state and runtime state

An A2A `COMPLETED` task means the work has a receipt. A related capability may still be pending,
quarantined, expired or revoked. Read its acceptance status before reuse. A2A observation
timestamps support pagination; the runtime's deadlines still use committed block heights.
An MCP prompt is review material, and tool annotations describe effects; neither is a signature.
These distinctions let clients use standard protocols without weakening the shared evidence model.
