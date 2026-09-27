"""Resolve an exact GitHub plan from a validated v2 dispatch reservation.

The supplied state must come from the operator's own validating node. A returned plan
is bound to that snapshot, not a transferable or indefinitely fresh execution token.
"""

from dataclasses import asdict, dataclass
from typing import cast

from checkedflow.core.operational import State
from checkedflow.core.values import Object, fields, integer, obj, require, text
from checkedflow.core.work_acceptance import fingerprint, status
from checkedflow.core.work_effects import Effect
from checkedflow.domains.repository_patch import Contract
from checkedflow.github_drafts import Plan
from checkedflow.operational_codec import decode, state_bytes
from checkedflow.repository_reuse import contract_digest
from checkedflow.wire import digest, document, validate


@dataclass(frozen=True)
class Intent:
    repository: str
    repository_id: int
    base_branch: str
    base_commit: str
    head_commit: str
    git_tree: str
    result: str
    target: str
    patch: str

    def __post_init__(self) -> None:
        fingerprint(self.target)
        self._plan("0" * 64, "0" * 64)

    def record(self) -> Object:
        return {"version": "checkedflow/github-effect-intent/v1", **obj(validate(asdict(self)))}

    @property
    def digest(self) -> str:
        return digest(self.record())

    def _plan(self, operation: str, authorization: str) -> Plan:
        return Plan(
            self.repository,
            self.repository_id,
            operation,
            authorization,
            self.patch,
            self.result,
            self.base_branch,
            self.base_commit,
            self.head_commit,
            self.git_tree,
        )


def reserved_plan(
    state: State,
    effect_id: str,
    intent: Intent,
    contract: Contract,
    *,
    executor: str,
    revision: int,
    policy: str,
) -> Plan:
    """Validate current reservation and immutable intent; do not contact or mutate GitHub.

    The separate executor must also verify operator policy bytes, current artifacts,
    source/patch bytes, local emergency disable and own-node freshness at send time.
    """
    state = decode(state_bytes(state))
    effect = next((item for item in state.effects if item.identity == effect_id), None)
    require(effect is not None, "NOT_FOUND", "effect reservation missing")
    # Narrowing is explicit without treating a caller-provided Python object as trusted state.
    effect = cast(Effect, effect)
    require(
        state.mode == "running" and effect.status == "dispatch_reserved",
        "STATE",
        "live running dispatch reservation required",
    )
    require(
        effect.fence == 1 and effect.reserved <= state.height < effect.until,
        "FENCE",
        "dispatch window differs",
    )
    require(
        effect.executor == executor and effect.revision == revision,
        "AUTHORITY",
        "effect belongs to another executor revision",
    )
    require(
        effect.intent == intent.digest and effect.policy == fingerprint(policy),
        "BINDING",
        "intent or policy differs from quorum approval",
    )
    candidate = next(item for item in state.candidates if item.identity == effect.candidate)
    require(
        status(candidate, state.credentials, state.height) == "accepted",
        "EVIDENCE",
        "candidate is no longer accepted",
    )
    require(
        candidate.target == intent.target == contract_digest(contract)
        and candidate.artifact == intent.result == contract.result_tree
        and intent.patch == contract.patch_digest,
        "BINDING",
        "accepted candidate differs from exact contract and patch",
    )
    require(
        contract.allow_draft_pr and effect.expires <= contract.deadline_height,
        "AUTHORITY",
        "contract excludes this draft or validity window",
    )
    require(
        contract.repository == intent.repository
        and contract.base_commit == intent.base_commit
        and contract.result_tree == intent.result,
        "BINDING",
        "intent differs from repository contract",
    )
    return intent._plan(effect.operation, effect.authorization)


def decode_intent(raw: bytes) -> Intent:
    """Decode complete intent metadata; decoding never grants authority."""
    require(len(raw) <= 8192, "LIMIT", "effect intent byte ceiling")
    record = document(raw)
    fields(
        record,
        "version repository repository_id base_branch base_commit head_commit "
        "git_tree result target patch",
    )
    require(
        record["version"] == "checkedflow/github-effect-intent/v1",
        "VERSION",
        "effect intent profile",
    )
    return Intent(
        text(record["repository"]),
        integer(record["repository_id"], low=1),
        *(
            text(record[name])
            for name in (
                "base_branch",
                "base_commit",
                "head_commit",
                "git_tree",
                "result",
                "target",
                "patch",
            )
        ),
    )
