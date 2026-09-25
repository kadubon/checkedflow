# Adapter contracts

## Agent protocols

`agents.gateway.Backend` defines only `state() -> State` and `submit(envelope) -> Object`.
The existing own-node RPC `Client` satisfies that contract. `Gateway(backend, chain, mission)`
adds scope, signature/transition preflight and committed-digest confirmation. A custom backend
must expose committed state from a trusted validating node; a remote API response is insufficient.

`agents.a2a.create_app(gateway, url, token, journal_path=..., push_hosts=..., grpc_url=...)`
provides A2A 1.0 JSON-RPC, HTTP+JSON and optional gRPC bindings. The `url`
is the advertised RPC endpoint; an operator hosting it behind a proxy must configure that endpoint
and transport security consistently. `agents.mcp.create_server(gateway)` provides the official
MCP SDK server with tools, resources/templates, prompt, completion and subscriptions.
`agents.mcp.create_http_app` adds HTTP/SSE with local bearer or OAuth resource authentication.
Both are optional imports. Neither owns keys,
node consensus or execution. [Agent communication](interoperability.md) specifies exact inputs,
outputs, application constraints, deployment examples and language-neutral payload schemas.

`Journal` persists A2A observation times, bounded history, authenticated cursor material and push
configurations. It is an index over committed state, never a competing ledger. Use one process
per private journal and retain it across restarts. `Push` validates callback policy separately
from delivery; `OAuth` implements the MCP TokenVerifier seam with provisioned public keys.
The [conformance matrix](conformance.md) maps those seams to official SDK tests. When embedding
the ASGI application, drive its lifespan so monitors and gRPC start and close correctly.

## ABCI and storage

Install `checkedflow[distributed]` to use the gRPC ABCI service. Generated protocol bindings
come from the CometBFT v0.40.0 module. Their
[manifest](../src/checkedflow/distributed/proto/manifest.json) records source hashes;
[generate_proto.py](../scripts/generate_proto.py) rebuilds bindings from included `.proto` files.
Generated imports stay within the CheckedFlow package to avoid top-level namespace collisions.

```sh
checkedflow abci --genesis genesis.json --database node/application.sqlite3 \
  --listen 127.0.0.1:26658
```

The service uses one lock around proposal/commit state. Proposal simulation has no write side
effects. SQLite WAL plus `synchronous=FULL` and one transaction preserve snapshot and block
outcomes together. Storage corruption fails closed. Do not share a database between validators.

`Store.blocks()` returns ordered block records. Each transaction retains exact bytes as hex
and its outcome code. Export them as `{"blocks": [...]}` and run:

```sh
checkedflow replay --genesis genesis.json --blocks blocks.json
```

This independently checks the recorded outcome of every command. CometBFT remains the authority
for consensus order; a supplied block file is not a signed consensus proof.

For long histories, stream `Store.iter_blocks()` to JSONL using `dumps(block,
string_limit=2097152) + b"\n"`. Pass that file with `--blocks-jsonl`. Each block remains bounded,
and empty blocks must remain in the stream. `Store.blocks()` is the convenience in-memory form.

## Generator

The bundled deterministic generator consumes one JSON object from stdin and produces one JSON
object on stdout. The [request schema](../src/checkedflow/data/generator.schema.json) has
`target`, `max_candidates`, `max_depth`, and `library` entries containing `id` and `operations`.

```sh
echo '{"target":["double","increment"],"max_candidates":256,"max_depth":3,"library":[]}' \
  | checkedflow generator
```

Output fields are `source`, `operations`, `dependencies`, `candidates_tried`. Source is Python
defining `solve(values)`. No generated source executes in the generator process. Search uses a
finite grammar interpreter; actual Python execution requires independent gVisor verification.

Custom generation tasks may supply `generator_argv` and `request` in `spec`. The worker launches
that exact argument array inside the fixed mission image and sends the request as JSON stdin.
The result must contain bounded `source`; other evidence is retained. External generator code
has the same isolation limits and cannot install packages or use an external network.

For the reference worker, task `spec.target` specifies the expected primitive composition.
The worker embeds the **actual admitted dependency source** during reuse. Grammar labels predict
the search result; exhaustive sandbox verification checks that the emitted source realizes it.
General domains require a new worker/checker adapter and a new immutable verifier contract.

## Runner and verifier

`GVisorRunner.run(argv, files, stdin)` returns a receipt: status, exit code, bounded stdout and
stderr, and reason. It accepts plain file basenames only. The launch profile fixes `runsc`,
network disabled, non-root UID 65534, read-only root and inputs, no capabilities, no-new-privileges,
one CPU, memory/swap ceiling, process limit, time limit and bounded output. The default 512 MiB
and 128-process ceiling include runtime overhead. Application data has a private `/tmp` tmpfs.

`GVisorRunner.python(source, inputs)` executes `solve` over the supplied finite input domain.
The registered reference verifier argv is exactly `["python", "-B", "-s", "/work/check.py"]`.
Expected outputs are computed and compared by the trusted worker outside the candidate sandbox.
The candidate can inspect the domain and checker wrapper, so this is an exhaustive-domain
behavior contract, not a secrecy-based challenge. Verifier attestations bind the source digest,
contract, verifier ID, image and behavioral digest.

No daemon, missing `runsc`, unsupported platform, timeout, output overflow or uncertain cleanup
produces checked evidence. Receipts retain a reason. There is no ordinary subprocess fallback
for submitted code. Custom external actuators are outside v1's bundled worker and must provide
idempotent requests, fence enforcement at the effect boundary and outcome reconciliation.

## Worker orchestration and recovery

`Worker.once()` first looks for its own completed generation receipt with source that has not
been proposed. It checks the source digest and resumes metadata admission. Otherwise it selects
one ready task, prioritizing verification, then commits lease and start before dispatch. The
reference generator persists `source`, `source_digest`, operations and candidate count in the
receipt. Older receipts containing only a digest need their original source supplied manually.
The bundled worker selects generation tasks only when it has both executor and producer roles:
the core requires the completed attempt's owner to propose its artifact.

Use one active process per signing identity. Independent identities may race for a task; only one
committed lease wins. A lost RPC reply may have followed a successful commit: query the same node
before deciding what happened. The worker never automatically executes an already started task
again. Malformed adapter specifications become charged unknown receipts with residuals.

A pending proposal may fail because its mission expired, its dependency was withdrawn or its
verification budget was consumed. Do not regenerate implicitly to make it pass. Inspect the task,
receipt and current mission; preserve them and authorize a new task when appropriate. The core
rechecks authority, scope and capacity on every proposal, including resumed proposals.

## Adding a domain

Define the input domain, output format and acceptance predicate first. Register an immutable
verifier ID, image and argv with three organization approvals. Implement a worker adapter that
produces the exact bound verification evidence, and test failure, disagreement, expiry and missing
infrastructure. Use `exhaustive=false` and unknown behavior identity if domain equivalence cannot
be established. Register a new mission using this verifier; never reinterpret an existing ID.

The external generator request schema describes the bundled grammar protocol. Custom generators
may define their own request within `spec.request`; they still emit bounded source for the worker
and run inside the fixed image. A custom generator must explicitly handle provenance and dependency
source. Merely passing operation labels to the standalone grammar command does not authenticate a
library or substitute for the reference worker's admitted-source reuse path.
