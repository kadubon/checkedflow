# Callback credential custody

A2A callback configuration contains two sensitive fields: the notification `token` and
`authentication.credentials`. CheckedFlow now encrypts the complete configuration before it enters
the notification table. Create, get and list responses omit those two fields; internal dispatch
decrypts them only to send the approved callback. URL and other configuration fields still appear
in responses: do not place credentials in a URL or arbitrary metadata.

This uses AES-256-GCM with a fresh random 96-bit nonce and the library's full authentication tag.
Associated data binds the journal's chain/mission identity and instance, task ID and configuration
ID. Moving encrypted bytes to a different row or journal fails authentication. See
[cryptography's authenticated-encryption contract](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM).
Encryption is outside consensus. [Client access policy](client-access.md) separately establishes
callback ownership and transport authorization when explicitly enabled.

## Provisioning

Persistent callback writes require an operator keyring. Pass its private file to
`checkedflow a2a --callback-key-file PRIVATE_KEYRING` along with the normal server options.
The file is a UTF-8 JSON object with exactly `active` and `keys`: `active` names the write key;
`keys` maps one to four ASCII alphanumeric key IDs (at most 32 characters) to 64 hex digits each.
Generate each 32-byte key with a cryptographic random generator. No default deployment key or
example live key is provided. The file must be at most 4 KiB.

Keep the file outside source checkouts, archives and the journal directory. Restrict its filesystem
ACL to the service identity and approved recovery operators. This SDK does not install OS ACLs or
claim HSM-backed custody. The host, process memory and operator-owned key file remain trusted.
Back up keys independently under the operator's secret-management policy. Do not put keys in CLI
arguments, logs, screenshots or issue reports.

A journal with no callbacks can operate without this file. Attempting a persistent callback write
without a keyring fails with `SECRET_KEY`. Reopening a journal that contains callbacks authenticates
all stored rows before service startup; a missing/wrong key or modified ciphertext prevents startup.
An in-memory journal uses an ephemeral in-memory key and has no restart durability claim.

## Rotation and recovery

The keyring's active ID affects new writes. Retain previous decrypting keys until every applicable
record and backup has been accounted for. The operator SDK method `Journal.rewrap(new_keyring)`
authenticates all existing rows and reseals them in one SQLite transaction while keeping delivered
fingerprints and attempt counts. A failure or process exit during this transaction leaves the old
rows intact. A new process still requires the keyring matching the committed state.

Perform rewrap as maintenance with other writers stopped; the supported journal has one owning
gateway process. Provision recoverable old/new keys before changing storage. If the process exits
after the database commit, reopen with both keys available and the intended new active key. Only
after successful validation and backup reconciliation may the old key be removed. Lost keys cannot
be recreated from consensus. This mechanism does not protect against rollback of an entire valid
journal/backup; independent freshness and coordinated recovery remain separate requirements.

Pagination revision binds encrypted configurations, so rotation/replacement invalidates old cursors
without exposing a digest computed directly from low-entropy plaintext credentials. Delivery updates
still compare the original configuration and exact stored ciphertext to avoid acknowledging a changed
configuration from an older in-flight request. They cannot cancel an already dispatched request.

## Legacy data and protocol behavior

Old plaintext notification rows fail with `SECRET_FORMAT`; there is no plaintext fallback or silent
in-place conversion. Writing new encrypted rows would not erase plaintext from old WAL pages,
snapshots or backups. Preserve the old journal privately for recovery, inventory its task/callback
state, rotate exposed callback credentials with the destination owner and establish a new protected
journal under an explicit migration procedure. Do not discard unresolved delivery obligations or
claim the general v1-to-v2 operational migration is implemented by this procedure.

The supported callback authentication is Bearer and uses the A2A 1.0 field
[`AuthenticationInfo.scheme`](https://a2a-protocol.org/latest/specification/#432-authenticationinfo),
case-insensitively. Other schemes and the obsolete plural `schemes` shape are rejected; they are
never reinterpreted as Bearer. Callback `token` is sent as `X-A2A-Notification-Token`.

## Evidence and limits

Tests cover encrypted database/WAL contents, redacted API responses, real restart, wrong keys,
ciphertext/AAD changes, malformed/oversized inputs, atomic rotation and actual process exit during
rotation. The installed-wheel v2 infrastructure case also stores and reopens a sealed fixture without
contacting an external callback. Exact-source qualification is required for that added path.

Client ownership and OAuth policy have separate [implementation and tests](client-access.md).
mTLS, controlled backup restoration and remaining G1-G7 requirements are not proved by encryption. This change fixes credential
storage/echo behavior; it does not qualify the complete 0.2.0 operational release.

The packaged structural contract is available through `checkedflow schema callback-keyring`.
Runtime validation additionally requires that `active` names an entry in `keys`.
