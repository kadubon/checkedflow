# ADR 0003: One durable submission coordinator per worker identity

Decision date: 2026-09-26. Status: accepted for implementation.

Worker lease, start, heartbeat, finish and verifier observations share one actor nonce stream.
Serialize them through an operator-owned local coordinator. Use a SQLite write lock in a separate
lock database to exclude other processes while the journal database can commit exact signed bytes
before network submission. Process death releases the lock, but never deletes the pending intent.
One signing identity must have one configured coordinator directory across all of its callers;
this local lock is not a distributed leader election or permission to share keys between workers.

Bind the journal to chain, mission and actor. Record monotonic observed height/nonce floors and
at most one outstanding signed command. Independently validate signer output through the ordinary
runtime before journaling. No administrative command is admitted by this worker adapter. Never
interpret an HTTP acknowledgment, a missing receipt or a timeout as proof of committed success or
absence. Confirm against the configured validating own node's matching committed receipt. Preserve
uncertainty across restart, key rotation and request-epoch retirement; do not invent a replacement
operation ID. Explicit bounded retransmission may send only the original byte sequence, after
rechecking the same current epoch/nonce and semantic eligibility. It cannot rerun candidate code.

Keep only the last confirmed command locally; authoritative historical records live in the
application journal. Failed or unknown sends remain pending until authenticated observation resolves
them. Older epochs without a supplied, current-root-anchored request archive remain unresolved. A complete
closed immediately preceding epoch can prove nonmembership and resolve as `retired_absent`, never
as successful execution.
Recovery is conservative: this component cannot clear an ambiguous request based on an empty query.
Supervised execution additionally needs a durable pre-execution record, freshness/fence checks,
bounded gVisor calls and separate result persistence. The coordinator alone does not execute work.
