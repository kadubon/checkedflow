# S3 artifact storage

Status: an implemented component of the unfinished 0.2 profile, not a qualified operational
deployment. Published 0.1.0 does not provide this API. The
[implementation record](implementation-0.2.0.md) identifies executed evidence and its scope.

## Problem and boundary

A recorded artifact hash is useful only if its bytes can be retrieved and checked. An object
store can lose an object, return corrupted bytes, deny access or lose a write acknowledgement.
`S3Store` treats these as different outcomes. It never substitutes a provider's ETag for the
declared SHA-256, and a successful request never becomes a verification vote.

The optional `s3` extra supplies Botocore for AWS Signature Version 4 and HTTPX for transport.
There is no handwritten signature algorithm or implicit AWS credential discovery. The adapter
implements the same `put`/`get` interface as [local storage](artifact-storage.md), with the same
portable reference and local authorization result. Neither backend belongs inside consensus.

## Explicit configuration

Install the development wheel with its `s3` extra in an isolated environment. For example:

```sh
uv pip install './dist/checkedflow-0.1.0-py3-none-any.whl[s3]'
```

This temporary filename reflects development metadata; it must never replace public 0.1.0.
Final distributions will use 0.2.0 only after mandatory qualification.

```python
from checkedflow.s3_artifacts import Credentials, S3Store

# Obtain arguments from trusted operator configuration and a secret provider.
def connect(endpoint, bucket, access_key, secret_key, ca_file):
    return S3Store(
        endpoint, bucket, Credentials(access_key, secret_key),
        region="us-east-1", prefix="checkedflow", ca_file=ca_file, timeout=10,
    )
```

The operator provisions the bucket and its policy separately. The adapter cannot create or list
buckets, delete objects or issue presigned links. The endpoint must be a fixed HTTPS origin without
credentials, path, query or fragment. Certificate and hostname validation are mandatory; a private
CA can be supplied. Only explicit test mode permits HTTP at literal address `127.0.0.1`.
Redirects, ambient proxy settings and compressed responses are rejected or disabled.

Objects use `/{bucket}/{prefix}/{scope}/{digest}`, with restricted portable component syntax.
`Access` must come from trusted current service policy, never unverified client claims. Local
authorization precedes reading an upload or contacting the provider. The provider must also enforce
its own least-privilege bucket/prefix policy. Provider credentials may confer wider access than
service-local policy; workers and candidate code must never receive them.

Credentials are explicit, excluded from their representation and omitted from adapter error messages.
Session tokens are supported. This component does not refresh credentials or protect against a
process with access to its memory. Construct a new adapter when trusted credentials change.
Never log authorization headers or place credentials in an artifact reference.

## Publication and retrieval

1. `put` checks local write authority and verifies the bounded input's exact length and SHA-256
   before any network write. Invalid or interrupted input cannot publish a partial object.
2. One signed `PUT` includes `If-None-Match: *`. The provider must implement conditional creation
   atomically; providers that ignore the condition are unsupported.
3. A successful response, conflict or lost response triggers a verified `GET`. Matching bytes
   reconcile the publication attempt. The adapter never automatically repeats the `PUT`.
4. `get` checks read authority, response bounds and exact plaintext bytes. Absent data is unavailable;
   wrong length or digest is an integrity failure, even when its ETag looks correct.

Internal read-back is part of publication and returns no object to a write-only caller. Provider
credentials nevertheless need read and conditional-write permission. Identical concurrent writes
can converge on the same verified bytes. Corrupted existing objects are not silently repaired.
Publication is a point-in-time observation: an administrator can subsequently remove the object.

| Outcome | Meaning |
|---|---|
| `AUTHORITY` | Local policy or provider denied access; this is not evidence of absence. |
| `INTEGRITY` | Length, digest or response ceiling failed; quarantine the observation. |
| `UNAVAILABLE` | A read received a non-success response other than access denial. |
| `TRANSPORT` | TLS, network, encoding or measured elapsed bound failed during a read. |
| `OUTCOME_UNKNOWN` | Publication could not be confirmed; preserve uncertainty and reconcile by a later authorized read. |

HTTP 500 does not prove a write never happened. Other unconfirmed write statuses also remain unknown.
No error creates a receipt, adoption, refund or retry authorization in the state machine.

## Bounds and omissions

References allow at most 4 MiB. Upload source reads are at most 64 KiB; response accumulation cannot
exceed reference length. PUT response bodies have a 4 KiB ceiling. Each call makes one PUT and at
most one reconciliation GET. There are no implicit provider retries.

Timeout is at most 30 seconds per network operation, with elapsed checks after headers and each
body chunk. This is **not a hard total wall-clock deadline**: blocking upload sources or drip-fed
headers can outlast it until the transport returns control. A worker supervisor must enforce the
overall attempt deadline and preserve uncertainty. The qualifier's outer process timeout is not a
production supervisor.

Per-scope quotas, replica receipts, availability admission, retention pins, tombstones, garbage
collection, encryption at rest, authenticated backup/restore and rollback protection are not
provided here. Bucket lifecycle rules must not remove retained evidence. No deletion API is exposed
before retention is implemented. Tests do not establish multi-host durability, cross-provider
consistency or an AWS-hosted deployment claim.

## Reproduce the real service check

The fixture uses checksum-pinned Apache-2.0 SeaweedFS 4.47 separately from the package. It creates
disposable private data, random credentials, an ephemeral CA and loopback listeners. Telemetry and
optional Iceberg/Lance listeners are disabled. It does not alter Docker, open public ports or execute
candidate code. Download is bounded to 64 MiB / 120 seconds; each readiness wait to 45 seconds.
Object recovery uses bounded read-only availability checks; it never resends a PUT. A bucket-list
response alone is not treated as data-plane readiness. Concurrent write acknowledgements can remain
unknown; the fixture preserves those outcomes and requires a subsequently verified read.
The installed test has a 180-second outer process-tree timeout; CI jobs have a 12-minute ceiling.

```sh
uv sync --frozen --all-extras --group dev
uv build
uv run python scripts/fetch_seaweedfs.py --directory .tools/seaweedfs
uv run python scripts/qualify_s3.py --dist dist --binary .tools/seaweedfs/weed
```

On Windows, use `.tools/seaweedfs/weed.exe`. The qualifier installs the supplied wheel with `[s3]`
in a fresh environment, verifies installed import origin and rejects missing, skipped or failed
cases. It records wheel digest and harness revision; dirty source is recorded as dirty. Its
`release_authority` remains false.

[Protocol tests](../tests/test_s3_artifacts.py) inject malformed responses and lost acknowledgements.
[Real service tests](../tests/test_s3_service.py) cover TLS, conditional concurrent writes, empty and
bounded objects, interrupted uploads, anonymous/wrong-credential/wrong-bucket denial, scope
separation, corruption, removal, outage and process-crash restart persistence. It also replaces
the provider credential during restart and checks old-credential denial and new-credential access.
This is not a power-loss durability or online provider rotation test. Mock transport results do not
qualify a provider.
CI tests the same built wheel on Windows and Linux; inspect actual results before claiming success.

Provider sources: [SeaweedFS 4.47](https://github.com/seaweedfs/seaweedfs/releases/tag/4.47),
[license](https://github.com/seaweedfs/seaweedfs/blob/4.47/LICENSE),
[S3 conditional PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html).
