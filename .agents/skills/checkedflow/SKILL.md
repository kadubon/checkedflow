---
name: checkedflow
description: Develop, inspect, test, and operate CheckedFlow's signed finite runtime, A2A/MCP gateways, CometBFT adapter, gVisor worker, research registry, and local package distribution. Use when changing this repository or handling CheckedFlow tasks and evidence.
---

Read the repository [operating guide](../../../skills.md) and then the
[protocol](../../../docs/protocol.md) for the affected command.
For an unfamiliar repository, use the [documentation index](../../../docs/README.md) and
[plan audit](../../../docs/audit.md) to locate concepts, implemented requirements and limits.

Use [commands.json](../../../src/checkedflow/data/commands.json) and packaged schemas as the
machine interface. Keep the core deterministic. Distinguish proposed, committed and independently
verified results. Preserve unknown outcomes and residual history. Run the required checks listed
in the operating guide and report their actual scope.

For agent connections, read [interoperability](../../../docs/interoperability.md) and the
[machine profile](../../../src/checkedflow/data/agents.json). Gateways transport original signed
JSON strings and observe committed state; they neither hold keys nor execute source. Preserve
mission scope and ambiguous outcomes. Keep protocol SDKs out of the core and test official clients.
Consult [conformance](../../../docs/conformance.md) and [security](../../../docs/security.md)
for protocol roles, callback/OAuth boundaries and publication scans. Persistent gateway journals
contain private credentials; they must never enter a source archive or public report.

Do not create repositories, push, tag, configure GitHub environments, dispatch workflows or
publish to PyPI without separate user authorization. Local implementation and package preparation
can proceed within the user's requested scope.
