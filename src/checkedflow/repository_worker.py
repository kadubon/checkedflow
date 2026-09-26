"""Concrete gVisor-only worker observation and scoped verified evidence publication."""

from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO

from checkedflow.artifact_io import Access, ArtifactStore
from checkedflow.core.artifact import Reference
from checkedflow.core.operational import State
from checkedflow.core.values import require
from checkedflow.core.work_tasks import Task, funding
from checkedflow.domains.repository_patch import Contract, Tree
from checkedflow.repository_execution import observe_patch
from checkedflow.repository_reuse import contract_digest
from checkedflow.wire import dumps, validate
from checkedflow.worker_supervisor import Outcome


@dataclass(frozen=True)
class RepositoryExecutor:
    """Operator-pinned inputs; every invocation uses the existing fail-closed gVisor runner."""

    base: Tree
    patch: bytes
    contract: Contract
    cases: bytes

    def __call__(self, state: State, task: Task) -> Outcome:
        ticket = funding(state.budget, task)
        require(ticket.phase in {"execute", "verify"}, "SCOPE", "repository observation phase")
        require(ticket.target == contract_digest(self.contract), "BINDING", "work contract differs")
        observation = observe_patch(
            self.base, self.patch, self.contract, self.cases, height=state.height
        )
        return Outcome(
            "unknown" if observation.case_match is None else "reported",
            dumps(validate(asdict(observation))),
        )


@dataclass(frozen=True)
class EvidencePublisher:
    """Publish and read back exact evidence. Integrity does not establish required replicas."""

    store: ArtifactStore
    access: Access

    def __call__(self, state: State, task: Task, evidence: bytes) -> str:
        fingerprint = sha256(evidence).hexdigest()
        reference = Reference(
            "sha256",
            fingerprint,
            len(evidence),
            "application/json",
            "evidence",
            state.mission,
            funding(state.budget, task).target,
        )
        self.store.put(reference, BytesIO(evidence), access=self.access)
        require(
            self.store.get(reference, access=self.access) == evidence,
            "INTEGRITY",
            "published evidence readback differs",
        )
        return fingerprint
