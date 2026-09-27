"""Pure v2 control, governed funding and bounded isolated-task ownership."""

from dataclasses import dataclass, replace

from checkedflow.core.authority import Credential, Verified
from checkedflow.core.key_registry import change as change_keys
from checkedflow.core.request_journal import Archive, Journal, Limits, Receipt, admit, rollover
from checkedflow.core.request_journal import genesis as journal_genesis
from checkedflow.core.values import Object, fields, integer, obj, require, text
from checkedflow.core.work_acceptance import VERIFIER_COMMANDS, Candidate
from checkedflow.core.work_acceptance import change as change_candidates
from checkedflow.core.work_acceptance import validate as validate_candidates
from checkedflow.core.work_archive import Head, WorkArchive, retire
from checkedflow.core.work_budget import Ledger
from checkedflow.core.work_budget import change as change_budget
from checkedflow.core.work_effects import COMMANDS as EFFECT_COMMANDS
from checkedflow.core.work_effects import EXECUTOR_COMMANDS, UNRESOLVED, Effect
from checkedflow.core.work_effects import advance as advance_effects
from checkedflow.core.work_effects import change as change_effects
from checkedflow.core.work_effects import validate as validate_effects
from checkedflow.core.work_tasks import WORKER_COMMANDS, Task
from checkedflow.core.work_tasks import advance as advance_tasks
from checkedflow.core.work_tasks import change as change_tasks
from checkedflow.core.work_tasks import validate as validate_tasks


@dataclass(frozen=True)
class State:
    chain: str
    mission: str
    organizations: tuple[str, ...]
    credentials: tuple[Credential, ...]
    journal: Journal
    height: int = 0
    mode: str = "paused"
    profile: str = "checkedflow/control-state/v2"
    budget: Ledger = Ledger()
    tasks: tuple[Task, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    history: Head = Head()
    effects: tuple[Effect, ...] = ()


def genesis(
    chain: str,
    mission: str,
    organizations: tuple[str, ...],
    credentials: tuple[Credential, ...],
    *,
    limits: Limits | None = None,
) -> State:
    text(chain, limit=128)
    text(mission, limit=80)
    require(
        len(organizations) == 4 and len(set(organizations)) == 4, "GENESIS", "four organizations"
    )
    for organization in organizations:
        text(organization, limit=80)
    require(4 <= len(credentials) <= 64, "GENESIS", "bounded initial identity slots required")
    require(
        len({credential.identity for credential in credentials}) == len(credentials)
        and len({credential.public_key for credential in credentials}) == len(credentials),
        "GENESIS",
        "distinct initial identities and keys required",
    )
    admins = [credential for credential in credentials if credential.role == "administrator"]
    require(
        len(admins) == 4
        and {credential.organization for credential in admins} == set(organizations),
        "GENESIS",
        "one initial administrator per organization required",
    )
    for credential in credentials:
        require(
            credential.organization in organizations
            and credential.revision == 1
            and credential.usable_at(0)
            and (credential.role == "administrator" or credential.mission == mission),
            "GENESIS",
            "initial credential scope or revision mismatch",
        )
    actors = tuple(credential.identity for credential in credentials)
    return State(
        chain,
        mission,
        tuple(sorted(organizations)),
        tuple(sorted(credentials, key=lambda credential: credential.identity)),
        journal_genesis(actors, limits or Limits()),
    )


def advance(state: State, height: int) -> State:
    integer(height, low=state.height)
    budget, tasks = advance_tasks(state.budget, state.tasks, height, state.credentials)
    validate_tasks(budget, tasks)
    budget, effects = advance_effects(
        budget, state.effects, state.candidates, state.credentials, height
    )
    validate_effects(
        budget, effects, state.candidates, tasks, state.credentials, state.mission, height
    )
    return replace(state, height=height, budget=budget, tasks=tasks, effects=effects)


def transition(
    state: State, command: Object, context: Verified
) -> tuple[State, Archive | WorkArchive | None]:
    """Context must come from authentication against this state, as in the v1 pure API."""
    require(state.profile == "checkedflow/control-state/v2", "VERSION", "unsupported state profile")
    fields(command, "api_version chain epoch id actor revision nonce kind payload")
    require(command["api_version"] == "checkedflow/v2", "VERSION", "v2 required")
    require(command["chain"] == state.chain, "CHAIN", "wrong chain")
    require(
        context.epoch == command["epoch"] == state.journal.epoch,
        "EPOCH",
        "context epoch mismatch",
    )
    require(
        context.actor.identity == command["actor"]
        and context.actor.revision == command["revision"]
        and context.actor in context.signers
        and all(
            signer in state.credentials and signer.usable_at(context.height)
            for signer in context.signers
        ),
        "AUTHORITY",
        "context does not match current registry",
    )
    require(context.height >= state.height, "HEIGHT", "height cannot decrease")
    kind = text(command["kind"])
    if kind not in WORKER_COMMANDS | VERIFIER_COMMANDS | EXECUTOR_COMMANDS:
        context.require_administration()
    require(
        kind
        in {
            "mission.pause",
            "mission.drain",
            "mission.resume",
            "journal.rollover",
            "history.archive",
            "key.schedule",
            "key.revoke",
            "budget.configure",
            "budget.reserve",
            "budget.settle",
            "budget.reconcile_inherited",
            "task.admit",
            "task.cancel",
            "artifact.admit",
            "artifact.revoke",
            *VERIFIER_COMMANDS,
            *WORKER_COMMANDS,
            *EFFECT_COMMANDS,
        },
        "VERSION",
        "command not supported by the initial v2 control profile",
    )
    payload = obj(command["payload"])
    if not kind.startswith(("key.", "budget.", "task.", "artifact.", "history.", "effect.")):
        fields(payload, "mission")
    require(payload.get("mission") == state.mission, "SCOPE", "mission mismatch")
    state = advance(state, context.height)
    receipt = Receipt(
        text(command["id"], limit=80),
        context.actor.identity,
        integer(command["nonce"], low=1),
        context.command_digest,
        kind
        not in {
            "budget.reserve",
            "task.admit",
            "artifact.admit",
            "effect.prepare",
            *WORKER_COMMANDS,
            *VERIFIER_COMMANDS,
            *EXECUTOR_COMMANDS,
        },
    )
    if kind == "journal.rollover":
        journal, archive = rollover(state.journal, receipt)
        return replace(state, journal=journal, height=context.height), archive
    journal, duplicate = admit(state.journal, receipt)
    if duplicate:
        return advance(state, context.height), None
    if kind == "history.archive":
        history, budget, tasks, candidates, batch = retire(
            state.history,
            state.budget,
            state.tasks,
            state.candidates,
            payload,
            epoch=state.journal.epoch,
            height=state.height,
            chain=state.chain,
            mission=state.mission,
            mode=state.mode,
        )
        validate_tasks(budget, tasks)
        validate_candidates(
            candidates, budget, tasks, state.credentials, state.mission, state.height
        )
        validate_effects(
            budget, state.effects, candidates, tasks, state.credentials, state.mission, state.height
        )
        return replace(
            state,
            history=history,
            budget=budget,
            tasks=tasks,
            candidates=candidates,
            journal=journal,
        ), batch
    if kind.startswith("artifact."):
        candidates = change_candidates(
            state.candidates,
            kind,
            payload,
            context,
            request=receipt.request,
            mode=state.mode,
            mission=state.mission,
            ledger=state.budget,
            tasks=state.tasks,
            credentials=state.credentials,
        )
        validate_candidates(
            candidates, state.budget, state.tasks, state.credentials, state.mission, context.height
        )
        return advance(replace(state, candidates=candidates, journal=journal), context.height), None
    if kind.startswith("effect."):
        budget, effects = change_effects(
            state.budget,
            state.effects,
            kind,
            payload,
            context,
            request=receipt.request,
            mode=state.mode,
            mission=state.mission,
            candidates=state.candidates,
            tasks=state.tasks,
            credentials=state.credentials,
        )
        return replace(state, budget=budget, effects=effects, journal=journal), None
    if kind.startswith("budget."):
        require(
            kind != "budget.settle"
            or (
                not any(task.ticket == payload.get("ticket") for task in state.tasks)
                and not any(effect.ticket == payload.get("ticket") for effect in state.effects)
            ),
            "BUDGET",
            "task-attached funding must settle through task lifecycle",
        )
        budget = change_budget(
            state.budget, kind, payload, request=receipt.request, running=state.mode == "running"
        )
        return replace(state, budget=budget, journal=journal, height=context.height), None
    if kind.startswith("task."):
        require(
            kind != "task.admit"
            or not any(effect.ticket == payload.get("ticket") for effect in state.effects),
            "BUDGET",
            "effect-attached funding cannot run a task",
        )
        budget, tasks = change_tasks(
            state.budget,
            state.tasks,
            kind,
            payload,
            context,
            request=receipt.request,
            mode=state.mode,
            mission=state.mission,
            credentials=state.credentials,
        )
        validate_tasks(budget, tasks)
        return replace(
            state, budget=budget, tasks=tasks, journal=journal, height=context.height
        ), None
    if kind.startswith("key."):
        credentials = change_keys(state.credentials, kind, payload, context)
        return advance(
            replace(state, credentials=credentials, journal=journal), context.height
        ), None
    require(
        kind != "mission.resume"
        or not any(effect.status in UNRESOLVED for effect in state.effects),
        "UNRESOLVED",
        "reconcile uncertain effects before resuming",
    )
    mode = {"mission.pause": "paused", "mission.drain": "draining", "mission.resume": "running"}[
        kind
    ]
    return replace(state, journal=journal, height=context.height, mode=mode), None
