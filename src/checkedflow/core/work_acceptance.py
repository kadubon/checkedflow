"""Contract-scoped quorum observations; acceptance never proves current availability."""

from dataclasses import dataclass, replace
from typing import cast

from checkedflow.core.authority import Credential, Verified
from checkedflow.core.values import Object, fields, integer, names, require, text
from checkedflow.core.work_budget import Ledger
from checkedflow.core.work_tasks import Task, funding

MAX_CANDIDATES = 64
VERIFIER_COMMANDS = frozenset({"artifact.attest", "artifact.withdraw"})


@dataclass(frozen=True)
class Observation:
    task: str
    actor: str
    revision: int
    organization: str
    height: int
    evidence: str
    verdict: str
    withdrawn: int = 0


@dataclass(frozen=True)
class Candidate:
    identity: str
    target: str
    artifact: str
    admitted: int
    expires: int
    checks: tuple[str, ...]
    observations: tuple[Observation, ...] = ()
    revoked: bool = False


def fingerprint(value: object) -> str:
    require(
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value),
        "BINDING",
        "canonical SHA-256 digest required",
    )
    return cast(str, value)


def status(candidate: Candidate, credentials: tuple[Credential, ...], height: int) -> str:
    """Caller supplies a validated committed candidate and its current registry."""
    if candidate.revoked:
        return "revoked"
    registry = {(item.identity, item.revision): item for item in credentials}
    if len({item.task for item in candidate.observations}) != len(candidate.observations) or any(
        observation.verdict == "fail"
        or observation.withdrawn
        or (observation.actor, observation.revision) not in registry
        or registry[(observation.actor, observation.revision)].revoked
        for observation in candidate.observations
    ):
        return "quarantined"
    if height >= candidate.expires:
        return "expired"
    organizations = {
        observation.organization
        for observation in candidate.observations
        if observation.verdict == "pass"
    }
    return "accepted" if len(organizations) >= 3 else "pending"


def _task(tasks: tuple[Task, ...], identity: str) -> Task:
    selected = next((task for task in tasks if task.identity == identity), None)
    require(selected is not None, "NOT_FOUND", "verification task missing")
    return cast(Task, selected)


def _organization(task: Task, credentials: tuple[Credential, ...], mission: str) -> str:
    organizations: set[str] = set()
    for worker in task.workers:
        owners = [item for item in credentials if item.identity == worker]
        require(
            bool(owners)
            and all(item.role == "verifier" and item.mission == mission for item in owners),
            "AUTHORITY",
            "check requires mission-scoped verifier identities",
        )
        organizations.update(item.organization for item in owners)
    require(len(organizations) == 1, "AUTHORITY", "one organization must own each check")
    return next(iter(organizations))


def change(
    candidates: tuple[Candidate, ...],
    kind: str,
    payload: Object,
    context: Verified,
    *,
    request: str,
    mode: str,
    mission: str,
    ledger: Ledger,
    tasks: tuple[Task, ...],
    credentials: tuple[Credential, ...],
) -> tuple[Candidate, ...]:
    if kind == "artifact.admit":
        context.require_administration()
        fields(payload, "mission target artifact expires checks")
        require(mode == "running", "PAUSED", "mission is not admitting artifacts")
        require(len(candidates) < MAX_CANDIDATES, "CAPACITY", "candidate retention capacity")
        require(
            not any(item.identity == request for item in candidates),
            "DUPLICATE",
            "candidate exists",
        )
        candidate = Candidate(
            request,
            fingerprint(payload["target"]),
            fingerprint(payload["artifact"]),
            context.height,
            integer(payload["expires"], low=context.height + 1, high=context.height + 1000000),
            tuple(sorted(names(payload["checks"], limit=4))),
        )
        require(len(candidate.checks) == 4, "QUORUM", "four funded organizational checks required")
        organizations = set()
        used = {check for item in candidates for check in item.checks}
        for check in candidate.checks:
            task = _task(tasks, check)
            ticket = funding(ledger, task)
            require(
                check not in used and task.status == "ready",
                "STATE",
                "check already used or started",
            )
            require(
                ticket.phase == "verify" and ticket.target == candidate.target,
                "BINDING",
                "check target differs",
            )
            organizations.add(_organization(task, credentials, mission))
        require(len(organizations) == 4, "QUORUM", "four distinct checking organizations required")
        return tuple(sorted((*candidates, candidate), key=lambda item: item.identity))
    require(
        kind in {"artifact.revoke", *VERIFIER_COMMANDS}, "VERSION", "unsupported artifact command"
    )
    fields(
        payload,
        "mission candidate"
        if kind == "artifact.revoke"
        else (
            "mission candidate task evidence verdict"
            if kind == "artifact.attest"
            else "mission candidate task"
        ),
    )
    selected = next((item for item in candidates if item.identity == payload["candidate"]), None)
    require(selected is not None, "NOT_FOUND", "candidate missing")
    candidate = cast(Candidate, selected)
    if kind == "artifact.revoke":
        context.require_administration()
        candidate = replace(candidate, revoked=True)
    else:
        context.require_role("verifier", mission)
        check = text(payload["task"], limit=80)
        require(check in candidate.checks, "SCOPE", "check outside candidate contract")
        task = _task(tasks, check)
        require(
            task.owner == context.actor.identity,
            "AUTHORITY",
            "only check owner may attest or withdraw",
        )
        prior = next((item for item in candidate.observations if item.task == check), None)
        if kind == "artifact.withdraw":
            require(prior is not None, "STATE", "no observation to withdraw")
            candidate = replace(
                candidate,
                observations=tuple(
                    replace(item, withdrawn=item.withdrawn or context.height)
                    if item.task == check
                    else item
                    for item in candidate.observations
                ),
            )
        else:
            verdict = text(payload["verdict"])
            require(
                verdict in {"pass", "fail", "unknown"}, "SHAPE", "unsupported observation verdict"
            )
            require(
                not any(
                    item.task == check and item.verdict == verdict
                    for item in candidate.observations
                ),
                "STATE",
                "observation is immutable; withdraw instead",
            )
            require(
                task.revision == context.actor.revision
                and (
                    task.status == "finished" or (task.status == "unknown" and verdict == "unknown")
                ),
                "STATE",
                "completed check under this key revision required",
            )
            evidence = fingerprint(payload["evidence"])
            require(
                evidence == task.evidence,
                "BINDING",
                "observation evidence differs from completed check",
            )
            observation = Observation(
                check,
                context.actor.identity,
                context.actor.revision,
                context.actor.organization,
                context.height,
                evidence,
                verdict,
            )
            candidate = replace(
                candidate,
                observations=tuple(
                    sorted(
                        (*candidate.observations, observation),
                        key=lambda item: (item.task, item.verdict),
                    )
                ),
            )
    return tuple(candidate if item.identity == candidate.identity else item for item in candidates)


def validate(
    candidates: tuple[Candidate, ...],
    ledger: Ledger,
    tasks: tuple[Task, ...],
    credentials: tuple[Credential, ...],
    mission: str,
    height: int,
) -> None:
    require(len(candidates) <= MAX_CANDIDATES, "CAPACITY", "candidate retention capacity")
    previous = ""
    used: set[str] = set()
    registry = {(item.identity, item.revision): item for item in credentials}
    for candidate in candidates:
        text(candidate.identity, limit=80)
        require(previous < candidate.identity, "STATE", "candidates must be unique and sorted")
        previous = candidate.identity
        fingerprint(candidate.target)
        fingerprint(candidate.artifact)
        integer(candidate.admitted, low=1, high=height)
        integer(candidate.expires, low=candidate.admitted + 1, high=candidate.admitted + 1000000)
        require(type(candidate.revoked) is bool, "SHAPE", "boolean revocation required")
        require(
            len(candidate.checks) == 4 and tuple(sorted(set(candidate.checks))) == candidate.checks,
            "STATE",
            "four unique sorted checks required",
        )
        organizations = set()
        for check in candidate.checks:
            task = _task(tasks, check)
            ticket = funding(ledger, task)
            require(check not in used, "STATE", "check used by multiple candidates")
            used.add(check)
            require(
                task.started == 0 or task.started >= candidate.admitted,
                "STATE",
                "check execution predates candidate admission",
            )
            require(
                ticket.phase == "verify" and ticket.target == candidate.target,
                "BINDING",
                "candidate check target differs",
            )
            organizations.add(_organization(task, credentials, mission))
        require(len(organizations) == 4, "QUORUM", "check organization scope differs")
        require(len(candidate.observations) <= 12, "CAPACITY", "observation bound")
        prior = ("", "")
        for observation in candidate.observations:
            require(
                prior < (observation.task, observation.verdict)
                and observation.task in candidate.checks,
                "STATE",
                "observations must be unique, sorted and scoped",
            )
            prior = (observation.task, observation.verdict)
            task = _task(tasks, observation.task)
            credential = registry.get((observation.actor, observation.revision))
            require(credential is not None, "STATE", "observation credential missing")
            owner = cast(Credential, credential)
            require(
                owner.organization == observation.organization
                and owner.identity == task.owner
                and owner.revision == task.revision,
                "AUTHORITY",
                "observation owner differs",
            )
            integer(observation.height, low=max(task.started, candidate.admitted), high=height)
            require(
                owner.activated_height <= observation.height
                and (owner.retired_height is None or observation.height < owner.retired_height),
                "AUTHORITY",
                "observation outside historical key window",
            )
            require(
                observation.verdict in {"pass", "fail", "unknown"}, "SHAPE", "observation verdict"
            )
            require(
                task.status == "finished"
                or (task.status == "unknown" and observation.verdict == "unknown"),
                "STATE",
                "observation without completed check",
            )
            require(
                fingerprint(observation.evidence) == task.evidence,
                "BINDING",
                "observation evidence differs",
            )
            integer(observation.withdrawn, high=height)
            require(
                observation.withdrawn == 0 or observation.withdrawn >= observation.height,
                "STATE",
                "withdrawal predates observation",
            )
