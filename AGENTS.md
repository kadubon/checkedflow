# CheckedFlow contributor instructions

Keep the core deterministic and independent of transport, storage, cryptography and runners.
Read docs/protocol.md before changing wire semantics. Preserve unknowns and unresolved obligations.
Do not confuse quorum agreement, checker acceptance, execution authority or external truth.
Run `uv run python scripts/check.py` before delivery. Integration qualification must run against
real CometBFT and gVisor; skipped tests cannot authorize publication.
Do not perform GitHub operations or publish to PyPI without explicit user authorization.
