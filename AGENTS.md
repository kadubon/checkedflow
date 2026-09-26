# CheckedFlow contributor instructions

Keep the core deterministic and independent of transport, storage, cryptography and runners.
Read docs/protocol.md before changing wire semantics. Preserve unknowns and unresolved obligations.
Do not confuse quorum agreement, checker acceptance, execution authority or external truth.
Run `uv run python scripts/check.py` before delivery. Integration qualification must run against
real CometBFT and gVisor; skipped tests cannot authorize publication.
Do not perform GitHub operations or publish to PyPI without explicit user authorization.

Operational v2 changes must update the requirement ledger and relevant human/machine guides.
Scheduling retries are not execution retries: preserve pending command bytes, execution records,
unknown outcomes, fixed call limits and boot-bound deadlines. Do not delete a journal or mint a
new task/plan identity to bypass recovery. Before the 0.2.0 release, audit README, every applicable
Docs page, this file and skills.md against actual installed-artifact behavior and G1-G7 evidence.
Keep the publication interlock closed while any mandatory gate remains unqualified.
