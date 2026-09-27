# ADR 0004: Separate effect intent, dispatch reservation and observation

Status: implemented for the unreleased v2 consensus profile; full actuator lifecycle remains open.

The provider journal prevents a local retained operation from automatically sending twice, but it
cannot establish organization approval, mission budget or current candidate eligibility. Add an
effect record to the v2 state, retaining empty-state serialization for previous v2 fixtures. V1
commands and hashes remain untouched. All participating v2 writers must understand the added
commands before activation; this change is not a rolling-upgrade protocol.

Preparation commits an immutable intent/policy digest and funded executor scope. A separate
administrative quorum authorizes it. Reservation makes a send possible, so it consumes the entire
modeled ceiling and can never return to an unstarted state. There is one fence/attempt; recovery
uses the original operation. Unknown absence cannot release funding or mint retry authority.
After a live reservation expires, only administrative reconciliation can record external evidence.

The core treats provider observations as attributed claims. It cannot decide remote truth from a
digest. The provider-specific intent resolver reconstructs exact GitHub arguments, while byte
binding compares source/patch trees. Actual network dispatch remains outside consensus and needs
current own-node freshness, protected policy, artifact availability and retained provider state.

Retained effects pin candidate and funding records. This initially bounds retained effects at 64
and explicitly leaves effect retirement incomplete; silently pruning uncertain obligations is not
an acceptable capacity strategy. Similarly, compensation-required is a preserved obligation, not
an assertion that the provider has already reversed an action. Implement and qualify those paths
before enabling the final operational release.

See [effect transitions](work-effects.md) for exact commands and limitations.
