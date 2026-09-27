# Client access for operational agents

A valid access token answers **which client is calling**. The private access policy answers **what
that client may do in this mission**. A signed command still needs current committed authority and,
for administration, signatures from three organizations. None of these checks substitutes for another.

The v2 CLI requires `--access-policy PRIVATE_POLICY`. It applies the same policy to A2A JSON-RPC,
HTTP+JSON and gRPC, and MCP tools, resources, prompts, completion and subscriptions. V1 retains its
published single-operator behavior. The direct SDK is a trusted process boundary: callers must pass
`policy=` to `Handler`, `create_server` or server constructors to obtain client restrictions.

## Policy and identity

The JSON file contains `profile: "checkedflow/access-policy/v1"` and a `grants` array. Each grant has
exactly `issuer`, `client`, `subject`, `chain`, `mission`, `roles`, and `actors`. The first five fields
form one unique binding. There are no wildcards, implied roles or client-supplied role assertions.
At most 128 grants fit in a 64 KiB file. Unknown fields, duplicate bindings/roles/actors, malformed
JSON, missing files and unsupported profiles fail closed. Update the file atomically under operator
control; restrict its OS permissions and backup access. The service does not change those permissions.

For an operator-token or stdio deployment, the identity is the fixed triple
`checkedflow:local`, `operator`, `operator`. Stdio is protected by process ownership. HTTP/gRPC
operator tokens come from the configured environment variable and are never accepted in message
metadata. Different clients on one HTTP service require OAuth; do not share an operator token when
client isolation is required.

For OAuth, the principal is the verified issuer, `client_id`, and `sub`. Both transports accept
`--oauth-issuer`, `--oauth-audience`, and `--oauth-jwks`; supply all three. The external issuer must
use HTTPS. Validation checks asymmetric signatures, issuer, audience, expiration and the
`checkedflow` scope. The operator installs the public JWKS; token-controlled key URLs are not fetched.
No authorization server, issuer deployment or token-issuing service is supplied. A2A clients present
an externally obtained Bearer token; MCP also supplies SDK resource-server discovery.

Example private policy for one local worker connection (replace the chain, mission and actor):

```json
{
  "profile": "checkedflow/access-policy/v1",
  "grants": [{
    "issuer": "checkedflow:local",
    "client": "operator",
    "subject": "operator",
    "chain": "my-chain",
    "mission": "array-mission",
    "roles": ["inspect", "submit"],
    "actors": ["worker-1"]
  }]
}
```

```console
checkedflow mcp --protocol v2 --rpc http://127.0.0.1:26657 --chain my-chain --mission array-mission --access-policy ./private/access.json
checkedflow schema access-policy
checkedflow schema access-roles
checkedflow schema access-vectors
```

The schema checks structure. Runtime checks additionally enforce unique identity/scope bindings,
file byte limits, the current principal's expiry, and the gateway's configured chain/mission.
The [portable decision vectors](../src/checkedflow/data/access-vectors.json) cover identity, role and
actor checks without conflating transport authorization with cryptographic signature validation.

## Permissions

The packaged [role contract](../src/checkedflow/data/access-roles.json) is the single command-to-role
mapping used by A2A and MCP. `inspect` is required for a gateway connection, including response data.
Additional roles permit submission of the following already-signed command categories:

| Role | Permitted category |
|---|---|
| `inspect` | Mission records, task views, protocol discovery and own notification configurations |
| `submit` | Worker lease, start, heartbeat and finish |
| `approve` | Task/candidate admission and budget reservation |
| `verify` | Verification attestation and withdrawal |
| `revoke` | Credential/artifact revocation and known-work cancellation |
| `operate` | Pause/drain/resume, key scheduling, budget configuration/settlement and bounded history retirement |

The command actor must also appear in that grant's `actors`. A transport role never signs a request.
Only the explicitly configured v2 CLI gateway enables administrative submission; the ordinary SDK
`Gateway` defaults to worker/verifier commands. A caller that deliberately enables SDK administration
must also install client policy. Consensus still checks organization quorum, actor purpose, current
key revision, mission scope, nonce and command-specific constraints. A rollover moves its own receipt
into an archive: the gateway confirms only an exact committed root matching its independently
predicted batch. Concurrent changes to that batch leave the result unknown until archive reconciliation. Unknown work cannot be cancelled
by assigning a transport `revoke` role.

## Revocation, notifications and callbacks

The policy file is reread for each operation and outbound HTTP chunk/gRPC response. OAuth keys are
rechecked at HTTP/gRPC egress. A2A streams recheck the principal while observing state. MCP subscriptions
retain the authenticated owner and check that owner's current grant before publishing updates; the
HTTP boundary checks again before sending buffered bytes. Revoked streams may terminate with an
incomplete response. Already-sent bytes cannot be recalled. Revocation can hide a reply after its command committed;
keep the original request identity and reconcile state after reconnecting instead of resubmitting new work.

A2A task cursors bind the client and policy revision. Callback cursors additionally bind a consistent
snapshot of the sealed configuration rows. Another client cannot reuse a cursor, overwrite/delete
someone else's callback, or retrieve its configuration. Each callback retains its creating principal
inside the [encrypted configuration](callback-secrets.md), and API responses omit that internal field.
Legacy configurations without ownership are invisible to policy-enabled clients and cannot dispatch;
retain and migrate them deliberately instead of silently assigning them to a new client.

Dispatch requires the owner's current inspection grant and its recorded expiry, and rechecks after
DNS resolution immediately before the network request. OAuth-backed callbacks stop when the creating
token's recorded authorization expires; an authorized owner must renew the configuration. Removing
a client grant also stops its callbacks. Deleting a JWT signing key alone does not revoke an existing
callback delegation: revoke its policy grant or configuration too. Denials preserve configurations
and consume the existing bounded delivery-attempt allowance; reconfiguration is explicit. No OAuth
access token is stored in the callback record.

## Evidence and remaining limits

Tests use actual A2A handlers, loopback HTTP/gRPC servers and official MCP clients. They check role,
actor, issuer/client/subject, cross-mission denial, live policy changes, callback ownership, cursor
binding, post-DNS revocation, stream egress, subscriptions and three-organization administration.
The installed-wheel CometBFT/gVisor worker case uses the same policy-enabled MCP server. Component
qualification is necessary but does not complete the operational deployment gates.

Inspection grants expose the shared mission; this is not per-record secrecy among approved readers.
Validators replicate the application state and remain part of the trust group. Artifact-store access
uses its separate `Access` contract: a unified authenticated download endpoint and archived-record
policy are still required. mTLS/proxy deployment, coordinated policy/key recovery, migration and full
G1-G7 evidence remain unfinished. Do not present this implementation as universal production readiness.
