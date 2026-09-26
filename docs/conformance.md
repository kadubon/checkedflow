# Protocol conformance and application boundaries

This is a server implementation of A2A 1.0 and MCP 2026-07-28 for the declared CheckedFlow
JSON application contract. “Complete” here means all standard A2A service operations and the
MCP server surfaces described below have concrete implementations and executable coverage.
It does not mean every agent's private extensions, every old protocol revision, every media
type, or every optional client role is implemented. There is no external certification claim.

The matrix below describes the published v1 application mapping. The development
[v2 gateway](operational-agents.md) shares these transport implementations, but has native task,
candidate and budget records, no agent-side administrative admission, and no unknown-work
cancellation transition. Its new protocol mapping and broader operational authorization are not
covered by historical v1 qualification or by this page's completeness statement.

The reviewed authorities are the [A2A specification](https://a2a-protocol.org/latest/specification/)
and [MCP specification](https://modelcontextprotocol.io/specification/2026-07-28). SDK versions
are locked in [uv.lock](../uv.lock). The machine profile is [agents.json](../src/checkedflow/data/agents.json).

## A2A service operations

All operations share the mission gateway and official SDK validation/error bindings.
JSON-RPC lives at `/rpc`, HTTP+JSON at `/v1`, and optional gRPC has its own configured port.

| Operation | Implementation | Direct evidence |
|---|---|---|
| SendMessage | Direct acknowledgments or tracked task; immediate/blocking modes | Official clients, blocking task and lost-ack tests |
| SendStreamingMessage | Initial Task, receipt artifact and status events; direct Message when appropriate | Real TCP SSE with JSON-RPC and HTTP+JSON clients |
| GetTask | Current projection, timestamp, bounded history and receipt | History/artifact and completion-not-acceptance tests |
| ListTasks | Descending observed time, inclusive filter, cursor, totals, artifact selection | Restart, clock rollback, changed/tampered cursor tests |
| CancelTask | Signed administrative abandonment of uncertain work | Missing quorum rejection and successful three-signature case |
| SubscribeToTask | Initial task then status/artifact events; disconnect cancellation | gRPC stream and handler terminal-state tests |
| CreateTaskPushNotificationConfig | Validated, bounded durable callback settings | Configuration CRUD and restart tests |
| GetTaskPushNotificationConfig | Scope-checked retrieval | Missing/configuration/tenant negative tests |
| ListTaskPushNotificationConfigs | Bound cursor pagination | Restart pagination and ordering test |
| DeleteTaskPushNotificationConfig | Scope-checked deletion | Read-after-delete failure |
| GetExtendedAgentCard | Authenticated card with mission description | Both HTTP bindings and protected-route tests |

Push dispatch additionally checks destination allowlists, public-only DNS, pinned IP with original
TLS hostname, dedicated callback headers, no redirects, bounded timeout and persistent attempts.
Tests mock the external HTTPS receiver so a test does not contact a third party. This verifies
the delivery contract and SSRF controls; it is not an Internet webhook provider certification.

Task history is a bounded observation history, not every committed block or arbitrary conversation.
Only declared JSON application parts are accepted. Cancellation may legitimately return
`TaskNotCancelable` under core authority/state constraints. Tenant routing is limited to the one
configured mission. A2A v0.3 compatibility is not advertised. gRPC is advertised only when enabled.

## MCP server role

| Surface | Implemented behavior | Evidence |
|---|---|---|
| Lifecycle/discovery | Official SDK modern discovery and legacy initialization | Real stdio and HTTP in both negotiation modes |
| Transports | Stdio, Streamable HTTP; legacy SSE for older hosts | Official clients over pipes and loopback TCP |
| Tools | Inspect and signed submit, input schemas, annotations, structured/text output, domain errors | Cross-protocol and invalid signature/scope tests |
| Resources | Three static resources and three mission-scoped templates | Discovery and template reads |
| Prompts | Evidence-review prompt carrying explicitly untrusted task data | Prompt listing/retrieval |
| Completion | Scope-filtered identity prefix suggestions | Prompt and template completion tests |
| Notifications | Modern `subscriptions/listen` resource updates | Live subscription/change/refetch test |
| Authentication | Local bearer or SDK OAuth resource-server middleware | Missing bearer, metadata, wrong scope, signed token tests |
| OAuth verification | Pinned public JWKS; issuer, audience, expiry, subject/client ID | Wrong claim, expiry, signature and key-rotation tests |
| Cancellation/errors | SDK request cancellation; work remains in committed state | Stream disconnect plus gateway unknown-outcome regressions |

Roots, sampling and elicitation are client-side facilities that a server may request for an
application need. CheckedFlow does not request local filesystem access, a model generation API,
or an interactive credential/approval dialog through MCP. Those capabilities are not advertised
as required. Extended task, UI or experimental protocol extensions are not part of this server
contract. The OAuth authorization server and host consent flow remain external; resource-server
support does not turn the gateway into a token issuer.

Modern notifications use `subscriptions/listen`; older resource-subscribe semantics are not
claimed. A lost notification stream must be reopened and resources reread. Static resource/tool/
prompt catalogs do not change during a process lifetime, so catalog-change events are unnecessary.

## Shared invariant coverage

[Gateway tests](../tests/test_agents.py) check signature admission, nonces, scope, duplicate bytes,
numeric ambiguity, unknown outcomes and revoked reuse. [Protocol tests](../tests/test_agent_protocols.py)
exercise the expanded surfaces above. [Four-node qualification](../tests/test_integration.py)
uses real consensus and a separate MCP process against a second node, confirming cross-protocol
idempotency and agreement. [Static checks](../scripts/static.py) prohibit protocol SDKs in the
core and common authentication/serialization modules. All vectors and schemas ship in the wheel.

See [validation status](validation-status.md) for actual runs and [security](security.md) for the
scope of the security audit. Protocol interoperability never substitutes for signature authority,
consensus finality or verification under a declared task contract.
