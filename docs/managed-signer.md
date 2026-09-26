# Managed Ed25519 signing with Vault Transit

Status: **IMPLEMENTED; REAL DISPOSABLE WINDOWS SERVICE TESTED**. The adapter and service test
are part of the unfinished 0.2 branch. This component does not qualify production Vault operation,
hardware custody, multi-host deployment or the full G4 gate. No Vault binary is bundled in a wheel
or sdist. The adapter uses the existing cryptography dependency and Python's standard HTTPS client.

## What the adapter does

[`VaultSigner`](../src/checkedflow/vault_signer.py) implements `Signer.sign(message)` and can be
passed to `sign_command` or `prove_possession`. It binds a selected committed `Credential` to an
operator-configured Vault origin, Transit mount, key name and explicit positive provider key version.
Application revision and Vault key version are separate numbers; neither is inferred from `latest`.

Before every signing request, the adapter reads public metadata for that exact service key. It
requires non-derived Ed25519, signing support, `exportable=false` and `allow_plaintext_backup=false`.
The selected version's public key must exactly equal the public key in the supplied credential.
The provider binding belongs to trusted operator configuration; endpoint addresses, tokens and
private keys are not inserted into consensus state. The committed public key remains the
cryptographic authority, and receiving runtime admission checks its current revision and purpose.

The adapter base64-encodes the exact domain-separated message and sends `key_version` explicitly
with `prehashed=false`. It does not sign a hexadecimal string or precompute a message digest.
Only the v2 command and key-possession domains are accepted, within a 1 MiB message ceiling.
The response must identify the selected provider version. The adapter decodes the signature and
locally verifies it against the exact message and credential public key before returning 64 bytes.
Changing provider `latest_version` therefore cannot silently change the signing identity.

These choices follow the [Transit signing API](https://developer.hashicorp.com/vault/api-docs/secret/transit).
The pinned service used here is [Vault 2.1.1](https://releases.hashicorp.com/vault/2.1.1/). Its executable
uses [BSL 1.1](https://raw.githubusercontent.com/hashicorp/vault/v2.1.1/LICENSE), separately from
CheckedFlow's Apache-2.0 license. The downloader verifies the official version-specific SHA-256
before extraction. Vault remains an external operator-managed service, not an embedded dependency.

## Configuration and trust

```python
import os
from checkedflow.vault_signer import VaultSigner

# credential is obtained from the caller's trusted committed identity registry.
signer = VaultSigner(
    credential=credential,
    endpoint="https://vault.operator.example:8200",
    key_name="checkedflow-administration",
    key_version=3,
    token=os.environ["CHECKEDFLOW_VAULT_TOKEN"],
    timeout_seconds=5,
)
```

Supply a custom trusted CA with `ca_file` when needed. TLS certificate and hostname validation stay
enabled; there is no `verify=False` setting. HTTP requires the explicit `allow_insecure_loopback=True`
test option and a literal loopback IP, not a hostname or a remote address. Origins cannot carry
credentials, a path, query or fragment. Mount and key names are single bounded ASCII path segments.
Redirects and environment proxy settings are not followed, so they cannot forward the token to
another origin. Namespace, derived-key and client-certificate configurations are outside this adapter.

Provision the Transit key separately with type `ed25519`, export disabled and plaintext backup
disabled. Grant the runtime token only these paths, adjusted to the selected mount/key:

```hcl
path "transit/keys/checkedflow-administration" {
  capabilities = ["read"]
}
path "transit/sign/checkedflow-administration" {
  capabilities = ["update"]
}
```

The adapter has no create, configure, rotate, export or backup method. Key administration and token
issuance/renewal remain separate operator responsibilities. Candidate code and gateways must never
receive this token or a signer object. A token enables service access; it does not substitute for
three-organization application governance or authorize a command on the receiving runtime.

The provider and its administrators remain trusted. Checking the non-export flags does not prove
that an administrator never copied key material previously or cannot compromise the service later.
The local test uses Vault development mode with disposable keys; it proves API behavior, not a
production storage, sealing, audit-log or hardware-security configuration.

## Failure and rotation behavior

Each network operation has a configured blocking-I/O timeout from 1 through 30 seconds. One `sign`
performs a metadata read followed by a signing request, without retries. The caller's supervisor
must additionally enforce its overall attempt deadline, including name resolution and service
availability; this adapter's socket timeout is not a distributed lease or a consensus clock.

Response bodies are bounded to 256 KiB. Encoded compression, oversized lengths, truncation, malformed
JSON, duplicate keys, incompatible metadata and wrong signatures fail closed. Error messages omit
provider response text, token values and local certificate paths. Token fields are excluded from
object representation. Do not enable HTTP debug logging or publish process dumps containing secrets.

| Code | Meaning and required response |
|---|---|
| `CONFIGURATION` | Invalid origin, path segment or token header; fix trusted configuration |
| `SIGNATURE` | Caller supplied an unsupported message domain or size |
| `SIGNER_AUTH` | Provider denied token access; no token or authority fallback |
| `SIGNER_BINDING` | Key metadata/version/public key/signature differs; stop signing |
| `SIGNER_RESPONSE` | Invalid, oversized or incomplete provider data |
| `SIGNER_UNAVAILABLE` | Transport, certificate or non-success provider failure; do not substitute a local key |

Provider rotation creates new material but does not authorize it in CheckedFlow. Read the new
public key through the operator's authenticated provider connection, configure a new signer with
its explicit version, obtain its possession proof, and use the governed
[application-key lifecycle](key-lifecycle.md). Keep the old provider version available only for the
declared overlap in operator procedures; application admission still stops it at its retirement
height. Raising Vault's minimum signing version can disable old signing earlier. It cannot change
historical signatures or create authority for the new key.

## Executable qualification

The bounded test profile uses one loopback-only TLS Vault process, two Go execution threads, a 256 MiB
Go soft memory target, a 20-second readiness ceiling and finite test operations. The target is not
an enforced process RSS limit. The executable is approximately 518 MiB on the tested Windows build;
download/extraction bounds are 256 MiB compressed and 1 GiB executable. No persistent daemon,
production token, model call or cloud resource is used. Development mode's token-file storage is
disabled, its environment is isolated from existing Vault settings, and teardown stops the child.

```sh
uv run python scripts/fetch_vault.py --directory .tools/vault
uv build
uv run python scripts/qualify_signer.py --dist dist --binary .tools/vault/vault
```

On Windows, use `.tools/vault/vault.exe`. The pinned downloads support Linux/Windows amd64 only.
The qualification script installs the exact supplied wheel and pytest in a fresh temporary
environment, confirms the package import is under `site-packages`, and runs the real service test
with isolated Python. A missing binary or skipped test fails the gate. It checks the official ZIP
hash and compares executable bytes to that archive; an editable provenance file alone cannot prove
provider identity. Installation has a 120-second ceiling, and the service-test subprocess tree has
a separate 120-second ceiling with forced cleanup on timeout.

The [real test](../tests/test_vault_service.py) verifies non-export policy, least-privilege denial,
exact bytes, pinned old/new versions, provider and governed application rotation, local signature
verification, revoked tokens and service outage. [Protocol-server tests](../tests/test_vault_signer.py)
separately cover malformed responses, TLS trust and host mismatch, redirects, timeouts, unsafe
configuration and secret redaction. These tests do not replace the real service case.

`reports/signer-installed.json` records wheel/provider hashes, harness revision/cleanliness and the
executed case. The harness revision is not an assertion about an arbitrary supplied wheel's build
source; release provenance must bind that separately. Raw logs
remain local/CI evidence. It explicitly disclaims release authority. The workflow's `managed-signer`
job runs the installed-wheel case on a fresh Linux runner and is a publication dependency. A green
signer job still does not satisfy the remaining S3, sandbox, recovery, multi-host or live-effect gates.
