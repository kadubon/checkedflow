# Operational identity boundary

Status: **SOURCE-TESTED AUTHENTICATION PRIMITIVE**. The complete v2 work state machine,
managed signing service, snapshot bootstrap and gateway integration are still pending. This API
does not turn a v1 node into an operational v2 deployment.

The initial [control runtime](operational-control.md) now connects this boundary to authenticated
pause/drain/resume and request-journal rollover. The [local store](operational-storage.md) persists
these control transitions. [Governed key scheduling and revocation](key-lifecycle.md) now use the
same boundary, with exact new-key possession proofs. Work execution and full work/archive
integration remain pending.

`checkedflow.operational_identity.authenticate` accepts original envelope bytes, not a decoded
SDK object. Strict lexical admission runs before signature verification. The signature message is
`CheckedFlow/command/v2`, a zero byte, then RFC 8785 canonical command JSON. The command fields are
`api_version`, `chain`, `epoch`, `id`, `actor`, `revision`, `nonce`, `kind`, `payload`; unknown fields
are rejected. The actor's key revision is signed as part of that command. Each signature envelope
entry carries `signer`, `revision`, `signature` and resolves against trusted committed registry data.

One identity may sign only once per envelope. The registry prohibits reuse of the same public key
across identities and revisions, including aliases that would otherwise substitute an unsigned
co-signer revision. Each registered key has one purpose and, except for organization administration,
one mission. CometBFT signing keys and gateway bearer credentials are not accepted signing purposes.
The supported purposes here are administrator, producer, executor, verifier and effect executor.

Credentials bind organization, revision, public key, activation height, optional retirement height
and revocation. Validity is inclusive at activation and exclusive at retirement. A revoked key
cannot authenticate a new submission. This predicate does not decide whether historical accepted
evidence should be withdrawn after compromise. The governed revocation remains in state/history;
work-level quarantine and dependency propagation still require implementation.

Authentication checks the exact chain and epoch. An older epoch returns `RETIRED_REQUEST`; a
future epoch returns `EPOCH`. It never turns an old request into a fresh command. Nonces and request
digests are carried for the state machine to enforce; this primitive alone has no replay database.
Its `Verified` result records the authenticated actor and principals. Administrative approval
requires an administrative actor and three different organizations with administrative keys.
Worker role checks also require exact mission scope. No bearer token or payload role flag counts.

`Signer.sign(message)` is a typed boundary for local or managed Ed25519 signing. The caller supplies
an explicitly selected identity/revision; the adapter returns the signature over those exact bytes.
No implemented Vault integration or non-exporting custody guarantee is claimed yet. `sign_command`
checks the signature size and builds bounded portable bytes; receiving authentication independently
verifies the signature using committed public keys.

The [tests](../tests/test_operational_identity.py) cover organization counting, purpose and mission
separation, inactive/revoked revisions, chain/epoch rejection, actor omission, signature tampering,
duplicate signatures, strict numbers/keys and v1/v2 signature-domain isolation. Legacy replay
continues to use the unchanged v1 implementation and its frozen original hashes.
