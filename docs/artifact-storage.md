# Artifact references and local storage

Status: a source-tested local component of the unfinished 0.2 operational profile. This is not
a network authorization service, an availability quorum, or a retention system. The separate
[S3 adapter](s3-storage.md) implements the same byte-store interface for an external service.
It is not connected to consensus work admission yet. The released 0.1 package does not contain it.

An artifact is a bounded sequence of bytes, such as a patch or a verification record. The
reference identifies those bytes and their intended context. The store keeps the bytes outside
the consensus state. A known digest does not authorize a caller to read another mission's data.

## Portable reference

The [reference schema](../src/checkedflow/data/artifact-reference.schema.json) and
[`Reference`](../src/checkedflow/core/artifact.py) require exactly these fields:

| Field | Meaning |
|---|---|
| `version` | `checkedflow/artifact/v1`; independent of command/package versions |
| `algorithm`, `digest` | `sha256` and lowercase SHA-256 of the exact plaintext bytes |
| `length` | Exact byte count, from zero through 4,194,304 |
| `content_type` | `application/json`, `application/octet-stream`, or `text/plain` |
| `kind` | `source-tree`, `patch`, `evidence`, `archive`, or `snapshot` |
| `scope` | Operator-assigned scope ID, 1–80 lowercase ASCII letters, digits, `_` or `-` |
| `manifest` | SHA-256 identity of the approved interpretation/contract manifest |

Scopes must be allocated without ambiguity across chains, missions and receivers. This component
does not derive that mapping from arbitrary client text. Credentials, URLs, provider endpoints,
encryption keys and expiring links never belong in a reference. Reference values alone do not
prove acceptance, availability, novelty or permission to execute anything.

The digest covers plaintext application bytes; it does not cover ciphertext, an S3 ETag or a
provider envelope. This local backend does not encrypt data at rest. An operator can use encrypted
storage beneath it; a future encryption adapter must still verify the plaintext reference after
decryption. Identical bytes can have different typed references. The contract interprets the type
and manifest; the byte store does not parse a source tree or validate evidence semantics.

## Local adapter

[`ArtifactStore`](../src/checkedflow/artifact_io.py) defines verified `put` and `get` operations.
The common module owns scoped access and bounded byte verification, with no SQLite, S3 or HTTP
dependency. The initial imports from `checkedflow.artifacts` remain available as SDK re-exports.
`LocalStore` uses a private filesystem-backed SQLite database, separate from the consensus store.
This choice avoids constructing filesystem paths from artifact names and makes object visibility
transactional on Windows and Linux. It is appropriate for the bounded 4 MiB object profile;
large-object and streaming-download optimizations are outside this initial implementation.

```python
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from checkedflow.artifacts import Access, LocalStore
from checkedflow.core.artifact import Reference

body = b"example evidence"
ref = Reference(
    "sha256", sha256(body).hexdigest(), len(body), "text/plain",
    "evidence", "demo", "1" * 64,
)
# The manifest above is illustrative; an operational caller must supply its approved digest.
access = Access("local-operator", frozenset({"demo"}), frozenset({"read", "write"}))
store = LocalStore(Path("private-demo") / "artifacts.sqlite")
store.put(ref, BytesIO(body), access=access)
assert store.get(ref, access=access) == body
```

`Access` is a trusted service-local policy result. It is not a token and must never be constructed
from unverified client claims. A network gateway must authenticate a client, enforce current
mission policy and expiry, and derive this value for each request. SDK callers already possess
the local database's operating-system authority. Candidate programs must receive neither the
store nor its directory. This component does not claim OAuth, mTLS or transport authorization.

Authorization runs before opening the database or consuming an upload stream. There is no
unauthorized existence probe. Objects are addressed by `(scope, digest)`; identical content in a
second scope is not visible there until separately published under authorized write access.
No presigned links or cross-scope cache exist in this adapter.

Uploads read at most 64 KiB per call, reject an overlong or nonbinary read, and verify total length
and digest before entering a database transaction. An interrupted or invalid upload publishes
nothing. The buffer has a 4 MiB declared ceiling plus at most one excess byte. A network stream's
time limits remain its adapter's responsibility; this synchronous API cannot interrupt a blocked
`read`. Validated bytes become visible at commit. SQLite serializes writers, so identical concurrent
puts converge on one row and competing new puts cannot evade per-scope capacity checks.

Defaults are 4,096 objects and 256 MiB per scope. Constructor overrides are persisted and must match
on reopening. A duplicate verified put succeeds at capacity. Capacity is checked within the write
transaction; separate scopes have separate accounting. Global disk quotas and the number of allowed
scopes remain operator admission controls. Exhaustion raises `CAPACITY`; it does not evict evidence.

Each read checks stored length before loading the blob, then verifies exact bytes against SHA-256
in one read transaction. Missing data raises `UNAVAILABLE`; altered bytes raise `INTEGRITY`.
A duplicate put also verifies the existing object and refuses silent corruption repair. Database
I/O failures propagate to the owning adapter, which must classify them as unavailable/unknown;
they never create a passing verification result. Do not open candidate-supplied SQLite files.

The store uses WAL and `synchronous=FULL`. Its directory and permissions must be managed by the
operator. This is not a defense against an administrator or hostile process with the same account
modifying the database. A backup must account for WAL; copying only the main file while it is active
is not a supported backup procedure.

## Evidence and remaining work

[Source tests](../tests/test_artifacts.py) exercise scope denial before I/O, absent cross-scope
content, exact bounds, interrupted/malformed streams, empty objects, concurrent puts, quota
admission, corruption, removal, SQLite rollback and reopen. These checks test local integrity and
visibility, not S3 consistency or multi-host durability.

The privileged `erase` method requires separate `erase` authority. It is a provider primitive,
not permission to bypass retention. Only the owning [retention controller](retention.md) should
receive that authority. Ordinary workers use its scoped interface and persistent pins instead.
Successful local `put` is not a signed replica-availability receipt. Full S3 deployment,
availability admission, lifecycle-root integration, authenticated restore and operational
qualification remain required before release.
