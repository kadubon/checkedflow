# Agent transports for the v2 operational runtime

The CLI selects the state/signature profile explicitly with `--protocol v1|v2`; the default remains
v1 for compatibility. Both profiles use the same A2A and MCP transport implementations. They differ
behind the `AgentGateway` interface, which provides scoped observations, command admission and
receipt confirmation. Protocol SDKs do not interpret the internal consensus state classes.

```console
checkedflow mcp --protocol v2 --rpc http://127.0.0.1:26657 --chain CHAIN --mission MISSION --access-policy PRIVATE_POLICY
checkedflow a2a --protocol v2 --rpc http://127.0.0.1:26657 --chain CHAIN --mission MISSION --access-policy PRIVATE_POLICY --journal PRIVATE_JOURNAL
```

MCP defaults to stdio with process-owner access. For HTTP deployments, use the existing
[interoperability](interoperability.md) and [security](security.md) configuration. A2A requires its
operator bearer token through the configured environment variable, or explicit OAuth settings.
The v2 CLI also requires [client policy](client-access.md); stdio uses the local process-owner grant. These examples do not supply
signing keys, start a validator, expose a public listener or enable external effects.

## Signed commands and authority

The v2 backend is `distributed.operational_client.Client`, connected only to the operator's
validating full node. Every read checks its synchronization state and the configured chain/mission.
Submission accepts the original UTF-8 envelope bytes; it never translates or resigns v1 data.
The temporary v2 runtime authenticates and evaluates the proposed transition before a single send.
The gateway returns a commit acknowledgment only after observing the exact command digest in the
committed request journal. Rollover can instead be confirmed by an exact committed archive root
matching the locally predicted batch containing its receipt. A different batch remains unknown.
An HTTP/RPC success alone is insufficient. A missing receipt, lost reply,
or unavailable post-send read returns `OUTCOME_UNKNOWN`, without automatic retransmission.

The ordinary SDK gateway exposes only worker/verifier commands. The v2 CLI additionally enables
already-signed administrative commands behind explicit [client roles and actor bindings](client-access.md).
Budgeting, admission, revocation and operations still require current committed administrative quorum. A bearer/OAuth token grants transport access, not a signing
role or permission to execute. Repeated commands still undergo current signature and admission
checks; an archived epoch or revoked key cannot use a cached acknowledgment to bypass them.

## Native records and transport projections

| View | v2 meaning |
|---|---|
| Mission | Committed mode, request epoch and actor nonces |
| Accounting | Native tickets and derived spent/reserved/available/protected verification values; modeled budget, not actual resource metering |
| Task | Native funding ticket, owner/revision/fence, block deadline, status and evidence digest |
| Capability route | Native candidate with acceptance status derived from current observations, keys and height |
| Residual route | No synthetic graph is created; absent records return `NOT_FOUND` |

An A2A finished task is a completion receipt; it does not imply the associated candidate was
accepted. Unknown work projects to input-required; cancelled work projects to cancelled. Evidence
artifacts contain the committed digest and an explicit `availability=not_implied`, not fetched
or fabricated evidence bytes. Unknown v2 work cannot be cancelled through this transport because
the corresponding governed reconciliation transition is not implemented; obligations remain.

MCP profile/schema discovery reports the selected command protocol. Resource reads, identity
completion and notification hints use mission-scoped snapshots. A snapshot reads the validating
node once, so records in one snapshot share a height/hash. Notification comparisons omit unrelated
block-height changes for individual records. Notifications are hints to reread, never execution
permits. A2A history remains bounded transport observations, not a consensus history or proof of
current reusability. Archived task observations may remain in that history.

The v2 A2A journal binds its version and the initial application identity roots. It cannot open a
v1 journal or a same-named chain with different identity roots. Callback configuration still has
its own [encrypted storage and recovery requirements](callback-secrets.md); it is not reconstructible
from consensus. Persistent callbacks require an operator-owned keyring.

## Qualification and outstanding security work

Source tests cover native views, current candidate revocation, original bytes, duplicate admission,
signature/epoch/scope rejection, definitive versus ambiguous outcomes, A2A task history and the
official MCP client. Existing v1 protocol tests remain. The installed-wheel v2 infrastructure test
now routes worker commands through MCP to actual CometBFT and inspects the resulting A2A evidence
projection. Only a successful run of that exact wheel qualifies this added path.

This increment does not complete the uniform operational security profile: authenticated artifact-download and archived-record authorization, coordinated callback-key backup/recovery, mTLS deployment, archived-record policy
and coordinated gateway recovery still require implementation/qualification against the frozen
0.2.0 specification. The implemented [client policy](client-access.md) must not be described as satisfying those wider
requirements. Full G1-G7 remain incomplete and publication stays disabled.

## Optional artifact reads

Both HTTP services can expose `/artifacts/<digest>` under their existing authentication and mission
policy. MCP additionally provides `checkedflow_read_artifact` when configured. Supply a private
publication catalog and existing local store; no digest implies permission. See
[authorized downloads](artifact-download.md) for byte bounds, revocation and archive trust limits.
