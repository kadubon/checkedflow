"""Bounded remote observation and published proposals for three-organization review.

No signatures, command submissions, provider writes or new dispatch identities are created.
Historical acceptance is sufficient to identify the original action, not to reuse its code.
"""

from dataclasses import dataclass
from io import BytesIO
from typing import cast

from checkedflow.artifact_io import Access, ArtifactStore, verify
from checkedflow.core.artifact import Reference
from checkedflow.core.operational import State
from checkedflow.core.values import require
from checkedflow.core.work_effects import Effect
from checkedflow.domains.repository_patch import Contract, apply_patch, digest_bytes
from checkedflow.effect_dispatch import Policy
from checkedflow.github_drafts import Drafts, Plan
from checkedflow.github_effects import Intent
from checkedflow.repository_reuse import contract_digest, decode_tree
from checkedflow.wire import dumps
from checkedflow.worker_submission import Coordinator


@dataclass(frozen=True)
class Proposal:
    """Canonical unsigned command and its published observation, never execution authority."""

    command: bytes
    evidence: Reference
    observation: bytes


class Reconciler:
    """One explicit read probe per collect call, including while dispatch is disabled.

    Supply the original executor's protected coordinator to pin own-node identity and prevent
    rollback, plus mission-scoped artifact access. A reader credential is sufficient for GitHub;
    this object never asks the coordinator to sign or submit. Administrators must independently
    review the retained evidence and approve the original effect through the normal quorum path.
    """

    def __init__(
        self,
        coordinator: Coordinator,
        provider: Drafts,
        store: ArtifactStore,
        access: Access,
        approved_policy: Policy,
    ) -> None:
        self.approved_policy = approved_policy
        self.coordinator, self.provider, self.store, self.access = (
            coordinator,
            provider,
            store,
            access,
        )

    @staticmethod
    def _effect(state: State, identity: str) -> Effect:
        found = next((item for item in state.effects if item.identity == identity), None)
        require(found is not None, "NOT_FOUND", "original effect is not retained")
        return cast(Effect, found)

    def _plan(self, state: State, effect: Effect, intent: Intent, contract: Contract) -> Plan:
        require(effect.fence == 1 and bool(effect.authorization), "STATE", "unreserved effect")
        require(
            effect.executor == self.coordinator.actor
            and effect.revision == self.coordinator.revision,
            "AUTHORITY",
            "recovery coordinator differs from original executor",
        )
        candidate = next(item for item in state.candidates if item.identity == effect.candidate)
        require(
            effect.intent == intent.digest
            and intent.target == candidate.target == contract_digest(contract)
            and intent.result == candidate.artifact == contract.result_tree
            and intent.patch == contract.patch_digest
            and intent.repository == contract.repository
            and intent.base_commit == contract.base_commit
            and contract.allow_draft_pr
            and effect.expires <= contract.deadline_height,
            "BINDING",
            "historical effect differs from original contract",
        )
        return intent._plan(effect.operation, effect.authorization)

    def collect(
        self, identity: str, intent: Intent, contract: Contract, base: Reference, patch: Reference
    ) -> Proposal:
        """Publish exact evidence before returning an unsigned effect.reconcile proposal.

        Pause, expired leases, withdrawn candidates and revoked executor signing keys do not
        authorize another send. They also do not prevent this historical read. A changed effect
        during I/O rejects the proposal; collect again explicitly if needed. A missing remote
        object is unknown, including when a prior positive number must remain pinned by the core.
        """
        state = self.coordinator.observe()
        effect = self._effect(state, identity)
        plan = self._plan(state, effect, intent, contract)
        require(
            self.approved_policy.authorize(
                state, intent, self.provider, effect.executor, effect.revision
            )
            == effect.policy,
            "POLICY",
            "retained policy differs from original quorum approval",
        )
        self.access.authorize(state.mission, "read")
        self.access.authorize(state.mission, "write")
        require(
            base.scope == patch.scope == state.mission
            and base.kind == "source-tree"
            and patch.kind == "patch"
            and base.manifest == patch.manifest == intent.target
            and patch.digest == intent.patch,
            "BINDING",
            "historical source references differ",
        )
        source = self.store.get(base, access=self.access)
        verify(base, source)
        tree = decode_tree(source)
        raw_patch = self.store.get(patch, access=self.access)
        verify(patch, raw_patch)
        apply_patch(tree, raw_patch, contract)
        outcome = self.provider.inspect_patch(plan, tree, raw_patch, contract)
        current = self.coordinator.observe()
        require(
            self._effect(current, identity) == effect, "CONFLICT", "effect changed during probe"
        )
        require(outcome.status in {"confirmed", "unknown"}, "SHAPE", "provider outcome")
        observed = outcome.status == "confirmed"
        require(
            type(outcome.number) is int
            and (outcome.number > 0 if observed else outcome.number == 0)
            and (not observed or effect.number in {0, outcome.number}),
            "CONFLICT",
            "provider object differs from original observation",
        )
        raw = dumps(
            {
                "profile": "checkedflow/effect-reconciliation/v1",
                "chain": state.chain,
                "mission": state.mission,
                "effect": identity,
                "intent": intent.digest,
                "policy": effect.policy,
                "fence": effect.fence,
                "prior_status": effect.status,
                "prior_evidence": effect.evidence,
                "prior_number": effect.number,
                "before_height": state.height,
                "after_height": current.height,
                "plan": plan.record(),
                "outcome": "observed" if observed else "unknown",
                "number": outcome.number,
            }
        )
        ref = Reference(
            "sha256",
            digest_bytes(raw),
            len(raw),
            "application/json",
            "evidence",
            state.mission,
            intent.digest,
        )
        self.store.put(ref, BytesIO(raw), access=self.access)
        verify(ref, self.store.get(ref, access=self.access))
        require(
            self._effect(self.coordinator.observe(), identity) == effect,
            "CONFLICT",
            "effect changed during publication",
        )
        return Proposal(
            dumps(
                {
                    "kind": "effect.reconcile",
                    "payload": {
                        "mission": state.mission,
                        "effect": identity,
                        "outcome": "observed" if observed else "unknown",
                        "number": outcome.number,
                        "evidence": ref.digest,
                    },
                }
            ),
            ref,
            raw,
        )
