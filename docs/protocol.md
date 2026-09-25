# Portable protocol v1

The packaged [JSON schemas](../src/checkedflow/data/envelope.schema.json),
[state schema](../src/checkedflow/data/state.schema.json),
[command catalogue](../src/checkedflow/data/commands.json) and
[conformance vectors](../src/checkedflow/data/vectors.json) are the interchange contract.

## Bytes and values

- UTF-8 JSON; reject duplicate keys at every depth, malformed Unicode and unpaired surrogates.
- Only null, booleans, strings, arrays, objects and integers are admitted. Reject decimal, exponent,
  NaN and infinity tokens. Integer magnitude is at most `9007199254740991`; booleans are not integers.
- There is no v1 decimal type. Future decimal support requires a versioned coefficient/scale contract.
- Canonical bytes follow RFC 8785, including UTF-16 key ordering. Preserve array order. Do not
  normalize Unicode. Whitespace and object insertion order do not change signatures.
- Strings are at most 262,144 UTF-8 bytes, nesting at most 32, and traversal at most 2,000,000 values.
  Signed transactions are at most 1 MiB. Snapshot/RPC JSON documents have a 16 MiB transport ceiling.
- Hashes are lowercase SHA-256 hex. Ed25519 keys are 32 bytes and signatures 64 bytes, encoded
  as lowercase hex. A source digest hashes the source's exact UTF-8 bytes.

## Authentication and authority

Sign exactly `b"CheckedFlow/command/v1\x00" + RFC8785(command)`. An envelope contains `command`
and `signatures`; each signature contains only `signer` and `signature`. Public keys are resolved
from the committed registry, never from an input-supplied certificate. Reject duplicate signers,
revoked keys, incorrect chain IDs, unsupported versions and unknown fields.

The command has `api_version`, `chain`, `id`, `actor`, `nonce`, `kind`, `payload`. The actor must
sign. Administrative commands require three distinct organization identities in the same envelope.
The actor nonce starts at one and advances only when a new command is admitted. Request ID reuse
with different canonical command bytes is `CONFLICT`; byte-identical resubmission is idempotent.

Authentication and semantic errors are stable codes such as `SCHEMA`, `SIGNATURE`, `VERSION`,
`CHAIN`, `NONCE`, `CONFLICT`, `QUORUM`, `AUTHORITY`, `BUDGET`, `FENCE`, `LEASE`, `DEPENDENCY`,
`SCOPE`, `BINDING`, `LIMIT`, `EXPIRED`, `EVIDENCE` and `STATE`. Human error text is explanatory
and not normative. CLI errors are JSON `{error, message}` on stderr with exit status 2; Python
exceptions never appear inside signed messages. Schema failure can precede semantic failure,
so consumers must not assume that every unknown version reaches the `VERSION` check.

## Agreement versus evidence

ABCI `CheckTx` is advisory admission. `PrepareProposal` evaluates transactions sequentially;
`ProcessProposal` repeats that evaluation; `FinalizeBlock` produces tentative results and hash;
`Commit` persists them. A query exposes only committed state. Historical queries and Merkle proofs
are unsupported. Connect only to your own validated full node over loopback or an authenticated
tunnel to it. Arbitrary public RPC replies are not execution authorization.

The state hash is SHA-256 of the canonical complete state. Block height is a protocol integer,
not a wall-clock deadline. In a quorum outage the height does not advance: no new work receives
finality, and external fencing/reconciliation remains the actuator's responsibility.

## Replay and block journals

There are two replay interfaces with different trust inputs:

| Interface | Input | Checks performed |
|---|---|---|
| `replay(initial, events)` | `(command, Context)` pairs and `BlockHeight(height)` values | Pure semantic transitions and expiry; contexts have already been authenticated. |
| `replay_blocks(initial, blocks)` | Exact transaction bytes and recorded outcome codes, grouped by height | Signature/schema checks, transitions, rejection outcomes and contiguous heights. |

The portable [block schema](../src/checkedflow/data/block.schema.json) defines each journal record:

```json
{"height":1,"transactions":[]}
```

A transaction entry is `{"raw":"...","code":"OK"}`. `raw` is the exact envelope bytes encoded
as lowercase, even-length hex; rejected non-JSON input also retains its original bytes. `code`
is `OK` or the actual stable rejection code. An empty raw transaction is representable and must
replay as a rejection. Unknown fields, block gaps and different outcomes reject the archive.

Apply height advancement once before processing each block, then process transactions in order.
An invalid transaction leaves its command changes unapplied; it does not undo the block's expiry
effects. Include empty blocks and blocks containing only rejected transactions. Hash the complete
resulting state using the same canonical serializer.

`checkedflow replay --blocks file.json` accepts `{"blocks":[...]}` within the 16 MiB document
ceiling. `--blocks-jsonl file.jsonl` streams one bounded block record per line, so total history
length is not constrained by that document ceiling. JSONL has no header or blank records.
The archive itself is not a signed block proof: establish its order from retained CometBFT
history or a trusted node-local journal before relying on its result.

The transport has distinct size units: source UTF-8 bytes, signed envelope bytes, hex journal
characters and base64 RPC characters. Base64 increases envelope size by roughly one third.
The laboratory RPC request-body ceiling is 2 MiB, enough for a 1 MiB transaction plus framing;
RPC serialization does not apply the smaller source-string limit to encoded transactions.
The ABCI boundary checks the original byte length before JSON parsing: padding a small canonical
command with whitespace cannot bypass the 1 MiB transaction limit or overflow its hex journal.

## Contract versioning

Agent transports wrap, rather than replace, this signed protocol. The separately versioned
`checkedflow-agents/v1` [profile](../src/checkedflow/data/agents.json) transports envelopes as
original JSON strings (`envelopeJson` in A2A, `envelope_json` in MCP). This preserves lexical
number checks and duplicate-key detection through SDKs that otherwise normalize JSON values.
After strict parsing, signatures still cover the canonical domain-separated command, not the
outer A2A/MCP request. RPC correlation IDs confer no authority. A2A `messageId` must also match
the signed command ID. See [agent communication](interoperability.md) for error mappings and
mission scope; core command/replay semantics remain `checkedflow/v1`.

Schemas reject unknown envelope fields. Domain-specific `spec` and receipt evidence are JSON
objects whose semantics belong to the selected adapter. A structurally valid state snapshot is
not proof of valid history; `serialization.decode` is for trusted local snapshots, and portable
validation should replay authenticated events.

The `empty_block_suffix` in the signed lifecycle vector records an additional height and expected
hash after expiry. A port must match both the command-only hash and this suffix. SDK-only additions
such as `BlockHeight` do not change signed v1 commands. Any change that reinterprets committed
command bytes requires a new protocol version and reviewed migration rules.
