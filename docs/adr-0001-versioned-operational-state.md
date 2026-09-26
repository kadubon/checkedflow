# ADR 0001: Preserve v1 and introduce an explicit operational profile

Decision date: 2026-09-26. Status: accepted design; implementation pending.

The 0.1.0 signatures, state hashes, expiry rules, errors and replay must retain their original
meaning. Package 0.2.0 will continue to expose that interpreter. New artifact references, identity
roles, epochs, archive commitments and effects belong to a separate `checkedflow/v2` command/state
profile. Their signature domain is `CheckedFlow/command/v2` followed by a zero byte and canonical
command JSON. No v1 command is automatically upgraded or accepted under v2 interpretation.

The v2 state genesis fixes the profile and capacities. Startup rejects unsupported profile/runtime
combinations. Signed commands bind chain, epoch, actor, actor nonce, identity revision, request ID,
kind and payload. Lexical JSON admission precedes any normalization by a transport SDK. Existing
integer bounds and RFC 8785 canonicalization remain common serialization rules, not a license to
reinterpret an old envelope. Authentication resolves keys from committed state, never the request.

New deterministic state and transitions live separately from the v1 modules. Adapters may share
strict parsing and cryptographic primitives; they must select the profile explicitly. No transition
calls a clock, storage service, signer, GitHub, telemetry exporter or sandbox.

Migration is a governed maintenance-window successor, not rewriting old histories. A transition
manifest must bind the frozen old chain/checkpoint and the new genesis, carry outstanding budget,
fences, revocations and unresolved work, stop old dispatch, and obtain current authority. Missing
old artifacts stay unresolved. Compatible binary updates retain quorum; incompatible state writers
cannot participate in the same active deployment. The operational migration path is not implemented
or qualified by this ADR alone.

Fixed fixtures must be captured using the published 0.1.0 runtime, including signed commands,
empty blocks, rejection codes, uncertainty and dependency revocation. New tests compare to these
original hashes; updating expected hashes to hide semantic changes is prohibited.
