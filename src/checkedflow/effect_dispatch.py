"""Supervised dispatch of an already committed, single-use effect reservation.

This module neither stages Git objects nor grants new authority. Operators provide a
protected policy, scoped artifact store, own-node watchdog and exclusive provider journal.
It does not reconcile unknowns or submit reports on the executor's behalf.
"""

from pathlib import Path

from checkedflow.artifact_io import Access, ArtifactStore, verify
from checkedflow.core.operational import State
from checkedflow.core.values import Failure, fields, integer, names, require, text
from checkedflow.core.work_acceptance import fingerprint
from checkedflow.dispatch_watchdog import Watchdog
from checkedflow.domains.repository_patch import Contract, Tree
from checkedflow.github_drafts import Drafts, Outcome, Plan
from checkedflow.github_effects import Intent, reserved_plan
from checkedflow.repository_reuse import Inputs, decode_tree, prepare
from checkedflow.wire import digest, document


class Policy:
    """Reload exact allowed intents from a protected operator file on every decision.

    Candidate code, transport clients and generators must not control this path or its
    parent directory. Canonical policy bytes must also be approved in the effect record.
    The fixed provider profile additionally requires disabled destination Actions.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def authorize(
        self,
        state: State,
        intent: Intent,
        provider: Drafts,
        executor: str,
        revision: int,
    ) -> str:
        try:
            with self.path.open("rb") as source:
                raw = source.read(16385)
        except OSError:
            raise Failure("POLICY", "protected effect policy unavailable") from None
        require(len(raw) <= 16384, "LIMIT", "effect policy byte ceiling")
        record = document(raw)
        fields(
            record,
            "profile chain mission repository repository_id actor "
            "executor revision enabled intents",
        )
        require(record["profile"] == "checkedflow/effect-policy/v1", "VERSION", "effect policy")
        require(record["enabled"] is True, "DISABLED", "effect policy disabled")
        allowed = names(record["intents"], limit=64)
        for identity in allowed:
            fingerprint(identity)
        require(tuple(sorted(allowed)) == allowed, "SHAPE", "sorted policy intents required")
        require(
            text(record["chain"], limit=128) == state.chain
            and text(record["mission"], limit=80) == state.mission
            and text(record["repository"]) == intent.repository == provider.repository
            and integer(record["repository_id"], low=1)
            == intent.repository_id
            == provider.repository_id
            and text(record["actor"], limit=80) == provider.actor
            and text(record["executor"], limit=80) == executor
            and integer(record["revision"], low=1) == revision
            and intent.digest in allowed,
            "POLICY",
            "effect is outside the current operator policy",
        )
        return digest(record)


class Dispatcher:
    """One bounded dispatch call using an existing consensus reservation.

    All supplied I/O adapters need bounded timeouts and protected storage. Cold/stale
    watchdogs fail closed; the caller supplies normal polling to establish progress.
    Provider outcomes are observations to report through the original-byte coordinator,
    not consensus receipts. Unknown or expired work requires governed reconciliation.
    """

    def __init__(
        self,
        provider: Drafts,
        watchdog: Watchdog,
        policy: Policy,
        store: ArtifactStore,
        access: Access,
        *,
        executor: str,
        revision: int,
    ) -> None:
        self.provider, self.watchdog, self.policy = provider, watchdog, policy
        self.store, self.access = store, access
        self.executor = text(executor, limit=80)
        self.revision = integer(revision, low=1)

    def _checked(
        self, effect: str, intent: Intent, contract: Contract, inputs: Inputs
    ) -> tuple[Plan, Tree, bytes]:
        self.watchdog.poll()
        state = self.watchdog.current()
        policy = self.policy.authorize(state, intent, self.provider, self.executor, self.revision)

        def resolve(current: State) -> Plan:
            return reserved_plan(
                current,
                effect,
                intent,
                contract,
                executor=self.executor,
                revision=self.revision,
                policy=policy,
            )

        plan = resolve(state)
        candidate_id = next(row.candidate for row in state.effects if row.identity == effect)
        candidate = next(row for row in state.candidates if row.identity == candidate_id)
        prepare(state, candidate_id, contract, inputs, self.store, access=self.access)
        base_bytes = self.store.get(inputs.base, access=self.access)
        verify(inputs.base, base_bytes)
        base = decode_tree(base_bytes)
        patch = self.store.get(inputs.patch, access=self.access)
        verify(inputs.patch, patch)
        # Storage reads cannot extend the authority observation. New evidence must be checked
        # by a later explicit call; never dispatch using an incompletely inspected inventory.
        self.watchdog.poll()
        current = self.watchdog.current()
        require(resolve(current) == plan, "BINDING", "reservation changed during artifact reads")
        require(
            next(row for row in current.candidates if row.identity == candidate_id) == candidate,
            "EVIDENCE",
            "candidate evidence changed during artifact reads",
        )
        require(
            self.policy.authorize(current, intent, self.provider, self.executor, self.revision)
            == policy,
            "POLICY",
            "operator policy changed during artifact reads",
        )
        self.watchdog.current()
        return plan, base, patch

    def dispatch(self, effect: str, intent: Intent, contract: Contract, inputs: Inputs) -> Outcome:
        """Recheck current policy/evidence both before I/O and at the final send boundary."""
        require(self.provider.enabled, "DISABLED", "GitHub effect provider disabled")
        plan, base, patch = self._checked(effect, intent, contract, inputs)

        def before_send(actual: Plan) -> None:
            checked = self._checked(effect, intent, contract, inputs)
            require(
                actual == plan and checked == (plan, base, patch),
                "BINDING",
                "final dispatch arguments changed",
            )

        return self.provider.dispatch_patch(plan, base, patch, contract, before_send=before_send)
