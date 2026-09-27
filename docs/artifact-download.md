# Authorized artifact downloads

A hash identifies bytes; it does not grant permission to read them. A storage account may contain
several missions' data. CheckedFlow therefore requires both a current client inspection grant and
an operator-owned publication catalog before returning an artifact. Downloads never execute content.

This is an optional v2 development feature. It uses the same [client policy](client-access.md) as
A2A and MCP. Without explicit configuration there is no artifact endpoint or MCP artifact tool.

## Configure publication

Keep the catalog in a private, operator-controlled directory, separate from candidate workspaces.
Export its portable structure with `checkedflow schema artifact-publication`. The complete document is:

```json
{
  "profile": "checkedflow/artifact-publication/v1",
  "chain": "my-chain",
  "mission": "array-mission",
  "artifacts": []
}
```

The empty catalog publishes nothing. Populate `artifacts` with complete
[artifact references](../src/checkedflow/data/artifact-reference.schema.json) after verifying their
mission provenance and deciding they may be shared with **all inspection clients of this mission**.
Each reference includes its digest, exact byte length, kind, content type, mission scope and manifest
digest. Use references from your trusted publisher; do not copy a candidate's assertions into the
catalog without review. A state pointer alone is not publication authority.

Catalogs are bounded to 256 references and 256 KiB. Digests must be unique. The catalog's chain and
mission must match the service policy exactly, and every reference's scope must equal that mission.
Unknown fields, duplicate JSON keys, invalid references and unavailable files fail closed.
Different chains with the same mission name need distinct reviewed catalogs and isolated storage
namespaces or databases. The operator's catalog is the authority for publication; it is not a
cryptographic proof of origin, agreement or acceptance.

Both existing agent CLI commands accept these additional arguments:

```console
--protocol v2 --access-policy ./private/access.json --artifact-catalog ./private/publication.json --artifact-store ./private/artifacts.sqlite
```

Supply both artifact arguments, and use an existing local artifact database. The CLI does not publish
all stored objects or create a missing database. SDK deployments may inject another `ArtifactStore`
through `Reader(store, catalog_path, policy)` and pass `artifacts=reader` to the A2A or MCP constructors.
Pass the **same Policy instance** to the reader and server. The underlying service receives read-only
`Access` authority for the current mission, with the verified client identity's digest as principal.
Provider integrity checks are repeated at this boundary.

## Read data

On either authenticated HTTP server, `GET /artifacts/<lowercase-sha256>` returns the exact bytes.
Use the same Bearer authentication as the agent API. Other methods and query parameters are rejected;
clients cannot supply paths, scopes, manifests, storage credentials or arbitrary references.
Responses use the catalog's content type, an attachment filename containing only the digest,
`X-Content-Type-Options: nosniff`, and `Cache-Control: no-store`. There are no redirects, public object
URLs, range requests, directory listings or unauthenticated download links. This route is a
CheckedFlow HTTP extension beside the A2A/MCP APIs, not a new method in either standard. A2A gRPC
clients use the same authenticated HTTP route for bytes.

MCP exposes the optional read-only tool `checkedflow_read_artifact` with `{"digest":"..."}`.
Its structured response contains `reference` and `base64`. Decode the base64 value and verify its
length and digest against the reference. This works over stdio as well as the supported HTTP
transports. Stdio uses the process owner's configured local principal.

An unpublished digest returns `NOT_FOUND` without querying storage. Missing or corrupt stored bytes
are unavailable, never silently repaired. HTTP returns 401 for missing authentication, 403 for
denied policy/publication, 404 for unpublished digests, and 503 for storage/integrity failures.
MCP returns tool errors for unavailable artifacts; its policy middleware denies unauthorized calls.

## Revocation and archived data

Policy and catalog are read before storage access and again after the read. Removing a grant,
removing the reference, or changing its metadata while storage is being read prevents delivery.
Update protected files atomically. HTTP additionally rechecks authentication and policy at egress;
MCP middleware checks policy before returning the operation. Already-delivered bytes cannot be
recalled. This mechanism does not physically erase data; use the separate retention controller.

An `archive` or `snapshot` reference uses exactly the same publication policy as current evidence.
Old data receives no automatic exemption, even if its task is no longer in active state. To serve
retired records, deliberately retain their exact archive bytes in the artifact store and publish
the reviewed reference. Download success establishes byte integrity and current read authorization,
**not** the archive's relationship to a trusted committed root. Verify that relationship with the
appropriate replay/checkpoint procedure before relying on the contents. Automatic publication of
consensus archive batches and indexed historical queries are not supplied by this component.

## Validation and limits

Tests exercise actual A2A/MCP HTTP applications and an official MCP client, including cross-client
and cross-mission denial, unknown digests, catalog bounds, mid-read withdrawal, altered reference
metadata, corrupt provider output and missing bytes. Isolated installed-package smoke tests exercise
publication and revocation. The download boundary has dedicated statement and branch coverage gates
and fault-injection tests for reference rechecking and provider integrity.

This catalog does not provide replicated availability, automatic retention roots, per-record secrecy
between approved mission readers, external TLS termination, or coordinated backup/restore of policy
and storage. Those remain separate operational responsibilities and release requirements.
