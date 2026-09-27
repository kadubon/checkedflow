# Porting the runtime

Start with the packaged schemas, canonical vectors and signed lifecycle vector. Python objects,
pickle formats and exception names are not part of the wire protocol.

An agent client can interoperate without porting the runtime. Implement the
[agent request schema](../src/checkedflow/data/agent-request.schema.json) using an A2A 1.0 SDK,
or discover MCP tools/resources over stdio or HTTP. Preserve the signed JSON string exactly and decode
`checkedflowJson` before interpreting state. Do not let a generic JSON/protobuf round trip rewrite
the inner command before CheckedFlow's strict parser sees it. [Agent communication](interoperability.md)
defines the profile's smaller service surface and testable errors. Porting the actual state
machine additionally requires every step below.

1. Implement bounded JSON parsing with duplicate-key rejection, safe integers, Unicode checks
   and RFC 8785 serialization. In JavaScript distinguish integers from floating tokens at parse
   time; `JSON.parse` alone loses that lexical distinction. Preserve UTF-16 canonical key order.
2. Implement SHA-256 and Ed25519 verification over the exact domain-separated canonical command.
   Resolve keys from committed state. Match admission ordering and stable error codes.
3. Implement the records in [state.schema.json](../src/checkedflow/data/state.schema.json).
   Clone or persistently update state; rejected transitions must leave the input unchanged.
4. Port the bounded transitions and deterministic expiry. Use block height as input. Reject
   integer overflow before updating a budget. Arrays retain declared order; dependency traversal
   has a stable ordering and finite depth. Do not depend on map iteration for app hashes.
5. Replay `vectors.json` and compare the complete canonical state hash, not only selected fields.
   Port the independent budget reference model and invariant-breaking fault tests.
6. Implement node-local atomic storage and the exact CometBFT 0.40 ABCI schema. Keep proposed
   state separate from committed state. Reproduce the four-node fault and recovery tests.
7. Implement the isolation adapter against the same fixed image and contract, with fail-closed
   behavior. Reproduce the sandbox resource, network and modification tests.

## A port's acceptance checklist

Compare canonical bytes and signature rejection before porting workflow logic. Then replay the
signed lifecycle and its `empty_block_suffix`: a port that only replays accepted commands will
miss final expiry. Preserve rejected transaction bytes and codes in block journals, and support
the distinct raw/hex/base64 size limits. JSONL recovery processes ordered records incrementally.

Preserve immutable dependency identities and visit shared ancestors once. The graph is acyclic
because dependencies must already be checked before proposal; mutating prior records would break
that argument. Distinguish a lineage round limit from the total number of independent tasks.

Implement arbitrary-width intermediate arithmetic or explicit checked arithmetic before updating
safe protocol integers. Reject floats lexically, including mathematically integral tokens such as
`1.0`. A schema alone cannot enforce duplicate-key rejection, Ed25519 authority, ancestry validity
or budget conservation; these remain boundary and transition checks.

Use [test_worker_recovery.py](../tests/test_worker_recovery.py) as an adapter-level failure contract:
a committed generation receipt resumes registration without invoking generation again. Do not
serialize Python dataclass names, exception classes, pickles or local path objects into your port.

The Python synthesizer emits Python source and is replaceable through the external JSON command
contract. A port may retain that generator while replacing the state machine. A different source
language, image or checker requires a newly approved immutable verifier definition and mission.
Changing a canonical rule or transition meaning requires a new protocol version and new vectors;
do not silently reinterpret old committed events.

## Operational effect interoperability

The [effect command schema](../src/checkedflow/data/effect-command.schema.json) and
[signed replay vector](../src/checkedflow/data/effect-flow-vector.json) define the new v2 boundary.
Replay every step against its expected state hash, including unknown observation, administrative
reconciliation and adverse-evidence compensation classification. Omit an empty `effects` collection
exactly as the Python encoder does; adding an empty array would change historical hashes. Preserve
full modeled charges once reserved and never interpret absent provider observations as retry rights.
The [GitHub intent schema](../src/checkedflow/data/github-effect-intent.schema.json) binds provider
arguments through canonical SHA-256 independently of the Git SHA-1 tree compatibility encoding.

The [effect policy schema](../src/checkedflow/data/effect-policy.schema.json) is a separate
operator-owned input. Its digest covers canonical JSON, with unique sorted intent digests;
an empty allowlist denies dispatch. Ports must reload it and revalidate scoped evidence and
own-node freshness after I/O, including at the provider's final send boundary. Preserve durable
unknown claims when that check fails. See [dispatch ordering](effect-dispatch.md#dispatch-ordering)
for the adapter algorithm and its non-atomic cross-system limits. These local checks never alter
deterministic block time or replace signed reservation authority.

Use the [effect observation schema](../src/checkedflow/data/effect-observation.schema.json) for
attributed provider results. Preserve canonical bytes, original provider plan and zero object
number for unknown results. The local invocation-height lower bound is not the external creation
time. Persist the invocation claim before entering an adapter; an interrupted claim must not call
the adapter again. Preserve original signed reports separately from provider receipts, and verify
stored evidence bytes before reporting. Local SQL phases are not consensus effect classifications.

Historical [reconciliation](effect-reconciliation.md) uses its own
[observation schema](../src/checkedflow/data/effect-reconciliation.schema.json). Bind the original
approved policy as well as the plan; a caller-selected provider actor is insufficient. Preserve
unknown lookup results and prior positive object identity. Publish and verify evidence before
returning the unsigned administrative proposal. A proposal is not a signed envelope, and historical
reads do not require or confer dispatch readiness. Current quorum admission remains a separate step.
