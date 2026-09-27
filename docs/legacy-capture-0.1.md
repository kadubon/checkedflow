# Frozen published-runtime compatibility capture

Captured on 2026-09-26 from PyPI `checkedflow==0.1.0`, installed into a fresh disposable
environment and invoked using Python isolated mode. The import origin was checked to be
`site-packages`, not the source tree. No new interpreter was used to calculate expected hashes.

The [packaged fixture](../src/checkedflow/data/legacy-v1.json) contains exact signed bytes,
genesis, 1,038 contiguous blocks, 43 state-hash checkpoints, final state and provenance.
Its SHA-256 is `04634fe3a57d6dc11a9668fa210fe964899fa07ebbc2b1899f883dc1988060c1`.
The final state hash is `9c12eea3393f018bbac48ad1660b1077c9e8fb6d1b6665eede9ca50cf1817950`.

This is a synthetic engineering history actually executed by the published runtime, not a
production consensus history or proof of four independent organizations. It uses the existing
test-only identity harness at baseline main, with no private keys in the fixture. The scenarios
cover governance, generation, checked parent/child capabilities, dependency withdrawal, a running
attempt becoming uncertain on an empty block, duplicate acknowledgment, duplicate JSON keys,
decimal-token rejection and an empty malformed transaction. It preserves the original rejection
codes and budget state. It does not claim exhaustive legacy coverage; existing signed vectors
and property tests remain required.

[Compatibility tests](../tests/test_legacy_capture.py) check the immutable fixture digest and
each original checkpoint, and reject altered outcomes, signed payloads and block gaps. The
installed wheel/sdist smoke also replays this fixture outside the checkout. Changes to v2 must
not update these expected hashes. Additional histories must be new captures with their own
provenance. Migration must carry the unresolved work and withdrawal without inventing evidence.
