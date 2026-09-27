# Changelog

## 0.2.0 — experimental operational tooling

- Add a separate v2 repository-patch workflow, independent checks and conditional reuse.
- Add durable worker/effect supervision, scoped A2A/MCP access, authenticated transport,
  artifact storage, managed signing, retention and migration preparation.
- Add reviewed deployment planning, preflight, non-overwriting application and local service control.
- Preserve v1 wire contracts and legacy replay. No automatic live migration is promised.
- Include monitoring templates, portable schemas, installed-artifact tests and measured load evidence.
- This release does not qualify the complete original G1–G7 operational profile. Cross-host key
  custody, node replacement, coordinated restore/upgrade and complete effect recovery remain
  unqualified. See [release scope](docs/release-0.2.0.md).

## 0.1.0

- Initial bounded deterministic runtime for signed work, shared budgets, residuals and conditional reuse.
- CometBFT 0.40 ABCI adapter, atomic SQLite persistence, authenticated replay and gVisor execution.
- Finite integer-array source generation, independent verification and reuse/scratch comparison.
- A2A 1.0 service operations over JSON-RPC, HTTP+JSON and optional gRPC, with durable observations,
  authenticated pagination, streams, authorized cancellation and secured push delivery.
- MCP tools, resources/templates, review prompt, completion, subscriptions, stdio/HTTP/SSE and
  OAuth resource-server integration.
- Portable schemas/vectors, 37-source research registry, English human/agent documentation,
  static checks, property/fault tests and required real-runtime release qualification.
- Apache-2.0 SDK/CLI distribution with type information and Trusted Publishing provenance.
