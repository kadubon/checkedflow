# Governed application-key lifecycle

Status: **UNRELEASED, SOURCE-TESTED COMPONENT**. Application-key scheduling and revocation now
execute through the initial v2 control runtime and its local atomic store. Full work/evidence
quarantine, archived key registries, managed signing, validator replacement and network deployment
remain required. These commands do not change CometBFT validator keys or signing-state files.

## Authority and ownership

An identity owns an organization, signing purpose, mission and monotonically increasing command
nonce. Rotation changes its public key and revision. It cannot move that identity to another
organization, change its purpose or reset its nonce. The initial profile has fixed identity slots;
new worker registration and purpose changes are not implemented by these commands.

Both operations require an administrative actor plus valid administrative signatures from three
distinct current organizations. The targeted old key need not participate: the other three
organizations can recover a lost administrative key. Two remaining organizations cannot authorize
recovery. Loss of quorum stops governance rather than creating a recovery credential that bypasses it.

Bearer credentials, the new key's possession proof and a prior quorum signature are not substitutes
for current administrative authority. Every envelope is authenticated against the current registry
at its processing height. Signature failure can precede duplicate-request acknowledgment.

## Commands and state changes

Use the [key-command schema](../src/checkedflow/data/key-command.schema.json) together with the
[v2 envelope contract](operational-identity.md). Both commands carry the normal chain, epoch,
request ID, administrative actor/revision and nonce. Their payloads have no additional fields.

| Command | Exact payload fields | Effect |
|---|---|---|
| `key.schedule` | `mission`, `identity`, `revision`, `public_key`, `activation_height`, `proof` | Add the next revision and schedule retirement of the previous one |
| `key.revoke` | `mission`, `identity`, `revision`, `reason` | Permanently deny that revision future command authority |

`key.schedule` requires the next consecutive revision, a never-used public key, and no still-pending
revision for that identity. Activation must be from one through 10,000 committed heights after
the processing height. This is a fixed limit in this unreleased profile. It is not a wall-clock
interval or a promise about how quickly the chain progresses.

At activation height H, the preceding revision's validity ends and the successor's begins:

| Revision | Height below H | Height H or later |
|---|---|---|
| Previous, not revoked | Usable, subject to its activation | Retired |
| Successor, not revoked | Pending; rejected for command signatures | Activated |

There is no interval in which both revisions of the same identity are usable. Different identities
can be on different revisions; a quorum still counts organizations. Every envelope admits only one
revision per signing identity. Routine retirement leaves the old credential record and original
signed history intact; it does not declare old observations false.

`key.revoke` accepts `reason="compromise"` or `reason="lost"`. The record is immediately revoked,
including when it was pending or already retired. The exact reason, target and processing height
remain in authenticated command/block history; the active credential carries the revoked flag.
A later rotation does not clear that flag on the older revision. Revoking a pending revision does
not cancel its scheduled retirement of the old one. The current implementation requires waiting
until that pending activation height before scheduling another successor. Operators must account
for this restriction; a complete cancellation/replacement procedure is still needed before the
operational recovery gate can pass.

For both reasons, consumers of historical evidence must treat the revoked revision as requiring
review/reverification. This component does not implement work acceptance or propagate quarantine
through capability dependencies yet. Preserving the revocation is not evidence that those pending
integrations have performed a review. Do not infer continued usability of an artifact merely from
its earlier acceptance or a successful successor-key rotation.

## Exact proof of possession

Create a `key.schedule` command with `proof` initially an empty string. The new key signs:

1. UTF-8 `CheckedFlow/key-possession/v2`, followed by a zero byte.
2. RFC 8785 canonical JSON of that entire command, with only `payload.proof` removed.

The proof therefore binds chain, epoch, request ID, proposer, proposer revision, nonce, target
identity, new revision, public key, mission and activation height. Encode the 64-byte Ed25519
signature as 128 lowercase hexadecimal characters. Administrative signatures then cover the usual
domain-separated full command, including this proof. No prehashing or hex encoding of the message
occurs before signing.

`operational_identity.prove_possession(command, signer)` returns a new command with the proof.
It does not mutate the input. `sign_command` subsequently collects administrative signatures.
The receiver verifies possession independently and passes the verified key into the pure transition
context. As with other pure transition contexts, callers must obtain it from authentication of the
same bytes against the same state. A client-supplied `Verified` object is never an authority source.

Changing the proposed activation, actor nonce, mission or any other signed field requires a new
proof. Proofs cannot be moved to a later request. A valid possession proof with inadequate
administrative approval still fails. Public-key reuse across any retained revision is rejected.

## Persistence, replay and capacity

The [local control store](operational-storage.md) derives changes from original signed bytes and
commits credentials, actor nonces, receipts and block history atomically. Its immutable ownership
check pins the initial identity roots and purpose/organization bindings while allowing governed
revision history to evolve. State decoding verifies consecutive revisions, ordering, immutable
ownership, distinct keys and activation/retirement continuity. It does not establish bootstrap trust;
signed replay and an independently trusted checkpoint are still necessary for that purpose.

Active state currently retains at most 1,024 key revision records. At this bound, new scheduling
fails without consuming a nonce; revocation, pause and receipt rollover remain available. This
bound protects state size but **does not satisfy sustained rotation or full-state longevity**.
Authenticated archival and historical compromise lookup must be integrated before claiming the
operational key lifecycle complete. Increasing this ceiling is not the planned solution.

## Validation scope

[Source tests](../tests/test_key_lifecycle.py) exercise pending/activation boundaries, old-key
rejection, mixed revisions, exact possession binding, inadequate quorum, key reuse, pending conflicts,
lost-signer recovery, nonce continuity, preservation of revoked history, saturation headroom,
local reopen and authenticated replay. Separate mutations remove possession and activation checks
to confirm that these tests detect their absence. These are application-level tests. No managed
signer, hardware custody, independent organizational operation or validator rotation is established.
