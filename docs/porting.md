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
