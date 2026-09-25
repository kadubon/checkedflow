"""Finite transitions. External facts enter only as scoped, authenticated attestations."""

import json
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256

from checkedflow.core.model import (
    BlockHeight,
    Capability,
    Context,
    Mission,
    Residual,
    State,
    Task,
    Verifier,
    Worker,
)
from checkedflow.core.values import (
    JSON,
    MAX_INT,
    Object,
    array,
    fields,
    integer,
    names,
    obj,
    require,
    text,
)


def _admin(s: State, ctx: Context) -> None:
    require(
        len(ctx.signers & s.organizations.keys()) >= 3, "QUORUM", "three organizations required"
    )


def _worker(s: State, actor: str, role: str) -> Worker:
    require(actor in s.workers, "AUTHORITY", "unknown worker")
    worker = s.workers[actor]
    require(not worker.revoked and role in worker.roles, "AUTHORITY", "worker role unavailable")
    return worker


def _mission(s: State, mid: str, actor: str = "") -> Mission:
    require(mid in s.missions, "NOT_FOUND", "unknown mission")
    mission = s.missions[mid]
    require(s.height < mission.expires, "EXPIRED", "mission expired")
    if actor:
        require(actor in mission.workers, "AUTHORITY", "worker outside mission")
    return mission


def _residual(s: State, rid: str, subject: str, reason: str, trigger: str) -> None:
    if rid not in s.residuals:
        s.residuals[rid] = Residual(subject, reason, trigger)


def _invalidate(s: State, cid: str, status: str, reason: str) -> None:
    queue = [cid]
    seen: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        cap = s.capabilities[current]
        cap.status = status if current == cid else "quarantined"
        for residual in s.residuals.values():
            if residual.resolution == current:
                residual.status = "open"
        for task in s.tasks.values():
            if task.subject == current and task.status in {"ready", "leased"}:
                if task.funded:
                    s.missions[task.mission].reserved -= task.cost
                    task.funded = False
                task.status = "abandoned"
        _residual(s, f"invalid:{current}:{cid}", current, reason, "fresh independent verification")
        queue.extend(
            key for key, child in sorted(s.capabilities.items()) if current in child.dependencies
        )


def _eligible(s: State, mid: str, dependencies: tuple[str, ...]) -> None:
    mission = s.missions[mid]
    pending = list(reversed(dependencies))
    visited: set[str] = set()
    while pending:
        cid = pending.pop()
        if cid in visited:
            continue
        visited.add(cid)
        require(cid in s.capabilities, "DEPENDENCY", "unknown capability")
        cap = s.capabilities[cid]
        source_mission = s.missions[cap.mission]
        require(
            cap.status == "checked" and s.height < cap.expires, "DEPENDENCY", "stale capability"
        )
        require(
            (
                source_mission.contract,
                source_mission.receiver,
                source_mission.verifier,
                source_mission.image,
            )
            == (mission.contract, mission.receiver, mission.verifier, mission.image),
            "SCOPE",
            "reuse contract or environment mismatch",
        )
        # Visit shared ancestors once; a dense DAG must not cause exponential work.
        pending.extend(reversed(cap.dependencies))


def advance(previous: State, height: int) -> State:
    """Apply committed block height, including blocks without valid transactions."""
    require(
        type(height) is int and previous.height <= height <= MAX_INT, "HEIGHT", "invalid height"
    )
    s = deepcopy(previous)
    s.height = height
    for cid, cap in sorted(s.capabilities.items()):
        if cap.status in {"candidate", "checked"} and height >= cap.expires:
            _invalidate(s, cid, "expired", "capability validity ended")
    for tid, task in sorted(s.tasks.items()):
        if task.status in {"leased", "running"} and height >= task.deadline:
            mission = s.missions[task.mission]
            mission.reserved -= task.cost
            # Missing metering is charged at the reserved upper bound, never zero.
            mission.spent += task.cost
            task.funded = False
            _residual(s, f"timeout:{tid}:{task.fence}", tid, "outcome unknown", "reconcile attempt")
            task.status = "uncertain"
    return s


def transition(previous: State, command: Object, context: Context) -> State:
    """Apply an authenticated command without mutating the supplied state or command.

    The caller must verify canonical command bytes and signatures before constructing Context.
    Use Runtime.apply or the ABCI adapter for untrusted input.
    """
    fields(command, "api_version chain id actor nonce kind payload")
    require(command["api_version"] == "checkedflow/v1", "VERSION", "unsupported protocol")
    require(command["chain"] == previous.chain, "CHAIN", "wrong chain")
    cid, actor, kind = text(command["id"]), text(command["actor"]), text(command["kind"])
    nonce, p = integer(command["nonce"], low=1), obj(command["payload"])
    require(actor in context.signers, "SIGNATURE", "actor signature required")
    require(len(previous.processed) < 4096 or cid in previous.processed, "LIMIT", "event limit")
    s = advance(previous, context.height)
    if cid in s.processed:
        require(s.processed[cid] == context.command_digest, "CONFLICT", "request identity reused")
        return s
    require(nonce == s.nonces.get(actor, 0) + 1, "NONCE", "nonconsecutive actor nonce")
    handlers = {
        "worker.register": _register_worker,
        "worker.revoke": _revoke_worker,
        "verifier.register": _register_verifier,
        "mission.create": _create_mission,
        "task.create": _create_task,
        "task.lease": _lease,
        "task.start": _start,
        "task.heartbeat": _heartbeat,
        "task.finish": _finish,
        "task.reconcile": _reconcile,
        "capability.propose": _propose,
        "capability.revoke": _revoke_capability,
        "residual.resolve": _resolve,
    }
    require(kind in handlers, "KIND", "unsupported command")
    handlers[kind](s, actor, p, context)
    s.processed[cid] = context.command_digest
    s.nonces[actor] = nonce
    for mission in s.missions.values():
        require(
            mission.spent >= 0
            and mission.reserved >= 0
            and mission.spent + mission.reserved <= mission.budget,
            "INVARIANT",
            "invalid budget balance",
        )
    require(
        len(json.dumps(asdict(s), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        <= 4_194_304,
        "LIMIT",
        "state admission capacity exhausted",
    )
    return s


def replay(initial_state: State, events: Iterable[tuple[Object, Context] | BlockHeight]) -> State:
    """Replay authenticated commands and committed heights in their original order."""
    state = deepcopy(initial_state)
    for event in events:
        if isinstance(event, BlockHeight):
            state = advance(state, event.height)
        else:
            command, context = event
            state = transition(state, command, context)
    return state


def _register_worker(s: State, actor: str, p: Object, ctx: Context) -> None:
    _admin(s, ctx)
    fields(p, "id key organization roles")
    wid, key, org = text(p["id"]), text(p["key"]), text(p["organization"])
    roles = names(p["roles"], limit=3)
    require(wid not in s.workers and wid not in s.organizations, "CONFLICT", "identity exists")
    require(org in s.organizations, "AUTHORITY", "unregistered organization")
    require(len(key) == 64 and all(c in "0123456789abcdef" for c in key), "KEY", "invalid key")
    require(key not in s.organizations.values(), "KEY", "administration and worker keys differ")
    require(key not in {w.key for w in s.workers.values()}, "KEY", "duplicate worker key")
    require(
        bool(roles) and set(roles) <= {"producer", "verifier", "executor"}, "ROLE", "invalid roles"
    )
    s.workers[wid] = Worker(key, org, roles)


def _revoke_worker(s: State, actor: str, p: Object, ctx: Context) -> None:
    _admin(s, ctx)
    fields(p, "id reason")
    wid, reason = text(p["id"]), text(p["reason"])
    require(wid in s.workers, "NOT_FOUND", "unknown worker")
    s.workers[wid].revoked = True
    for cid, cap in sorted(s.capabilities.items()):
        if any(v.get("worker") == wid for v in cap.votes.values()):
            _invalidate(s, cid, "quarantined", reason)


def _register_verifier(s: State, actor: str, p: Object, ctx: Context) -> None:
    _admin(s, ctx)
    fields(p, "id contract image argv exhaustive")
    vid = text(p["id"])
    require(vid not in s.verifiers, "CONFLICT", "verifier definitions are immutable")
    require(type(p["exhaustive"]) is bool, "SHAPE", "exhaustive must be boolean")
    argv = tuple(text(v) for v in array(p["argv"], limit=32))
    require(bool(argv), "SHAPE", "entrypoint required")
    image = _image(p["image"])
    s.verifiers[vid] = Verifier(text(p["contract"]), image, argv, p["exhaustive"] is True)


def _image(value: JSON) -> str:
    image = text(value)
    require(
        len(image) > 72
        and image[-72:-64] == "@sha256:"
        and all(c in "0123456789abcdef" for c in image[-64:]),
        "IMAGE",
        "image must have an immutable sha256 digest",
    )
    return image


def _create_mission(s: State, actor: str, p: Object, ctx: Context) -> None:
    _admin(s, ctx)
    fields(
        p,
        "id workers budget verification_reserve verification_cost expires max_rounds "
        "max_candidates max_depth max_attempts verifier receiver contract image",
    )
    mid, workers = text(p["id"]), names(p["workers"], limit=64)
    require(mid not in s.missions, "CONFLICT", "mission exists")
    require(
        bool(workers) and all(w in s.workers and not s.workers[w].revoked for w in workers),
        "AUTHORITY",
        "mission has invalid workers",
    )
    verifier = text(p["verifier"])
    require(verifier in s.verifiers, "VERIFIER", "verifier must be registered first")
    contract = text(p["contract"])
    require(s.verifiers[verifier].contract == contract, "SCOPE", "verifier contract mismatch")
    orgs = {s.workers[w].organization for w in workers if "verifier" in s.workers[w].roles}
    require(orgs == s.organizations.keys(), "QUORUM", "four verification organizations required")
    cost = integer(p["verification_cost"], low=1)
    budget = integer(p["budget"], low=1)
    reserve = integer(p["verification_reserve"], low=4 * cost, high=budget)
    s.missions[mid] = Mission(
        workers=workers,
        budget=budget,
        verification_reserve=reserve,
        verification_cost=cost,
        expires=integer(p["expires"], low=s.height + 1),
        max_rounds=integer(p["max_rounds"], low=1, high=16),
        max_candidates=integer(p["max_candidates"], low=1, high=256),
        max_depth=integer(p["max_depth"], low=1, high=16),
        max_attempts=integer(p["max_attempts"], low=1, high=16),
        verifier=verifier,
        receiver=text(p["receiver"]),
        contract=contract,
        image=_image(p["image"]),
    )


def _create_task(s: State, actor: str, p: Object, ctx: Context) -> None:
    fields(p, "id mission phase spec dependencies cost ttl effect")
    tid, mid = text(p["id"]), text(p["mission"])
    require(len(tid) <= 160, "LIMIT", "task identity length")
    _worker(s, actor, "producer")
    mission = _mission(s, mid, actor)
    require(tid not in s.tasks, "CONFLICT", "task exists")
    require(len(s.tasks) < 4096, "LIMIT", "task limit reached")
    phase, effect = text(p["phase"]), text(p["effect"])
    require(phase in {"generate", "execute", "repair"}, "PHASE", "invalid task phase")
    require(effect in {"isolated", "external"}, "EFFECT", "invalid effect class")
    deps = names(p["dependencies"], limit=16)
    _eligible(s, mid, deps)
    s.tasks[tid] = Task(
        mid,
        phase,
        deepcopy(obj(p["spec"])),
        deps,
        integer(p["cost"], low=1, high=mission.budget),
        integer(p["ttl"], low=1, high=10000),
        effect,
    )


def _owned(s: State, actor: str, p: Object) -> tuple[str, Task, Mission]:
    tid = text(p["id"])
    require(tid in s.tasks, "NOT_FOUND", "unknown task")
    task = s.tasks[tid]
    mission = _mission(s, task.mission, actor)
    _worker(s, actor, "verifier" if task.phase == "verify" else "executor")
    require(
        task.owner == actor and task.fence == integer(p["fence"], low=1),
        "FENCE",
        "stale or foreign lease",
    )
    require(
        task.status in {"leased", "running"} and s.height < task.deadline,
        "LEASE",
        "lease is not live",
    )
    return tid, task, mission


def _lease(s: State, actor: str, p: Object, ctx: Context) -> None:
    fields(p, "id")
    tid = text(p["id"])
    require(tid in s.tasks, "NOT_FOUND", "unknown task")
    task = s.tasks[tid]
    mission = _mission(s, task.mission, actor)
    worker = _worker(s, actor, "verifier" if task.phase == "verify" else "executor")
    require(task.status == "ready", "LEASE", "task is not ready")
    require(
        not task.organization or task.organization == worker.organization,
        "AUTHORITY",
        "wrong verification organization",
    )
    require(task.attempts < mission.max_attempts, "ATTEMPTS", "retry limit")
    _eligible(s, task.mission, task.dependencies)
    remaining = mission.budget - mission.spent - mission.reserved
    protected = mission.verification_reserve if task.phase != "verify" else 0
    if not task.funded:
        require(task.cost <= remaining - protected, "BUDGET", "insufficient unreserved budget")
    if task.phase == "verify":
        require(
            s.capabilities[task.subject].status in {"candidate", "checked"},
            "CAPABILITY",
            "subject is unavailable",
        )
    if not task.funded:
        mission.reserved += task.cost
        task.funded = True
    task.status, task.owner = "leased", actor
    task.fence += 1
    task.attempts += 1
    task.deadline = min(s.height + task.ttl, mission.expires)


def _start(s: State, actor: str, p: Object, ctx: Context) -> None:
    fields(p, "id fence")
    _, task, mission = _owned(s, actor, p)
    require(task.status == "leased", "LEASE", "attempt already started")
    _eligible(s, task.mission, task.dependencies)
    task.status = "running"
    mission.uses += len(task.dependencies)


def _heartbeat(s: State, actor: str, p: Object, ctx: Context) -> None:
    fields(p, "id fence")
    _, task, mission = _owned(s, actor, p)
    task.deadline = min(s.height + task.ttl, mission.expires)


def _finish(s: State, actor: str, p: Object, ctx: Context) -> None:
    fields(p, "id fence result")
    tid, task, mission = _owned(s, actor, p)
    require(task.status == "running", "LEASE", "execution has not started")
    result = deepcopy(obj(p["result"]))
    fields(result, "outcome evidence")
    outcome = text(result["outcome"])
    require(outcome in {"reported", "pass", "fail", "unknown"}, "OUTCOME", "invalid outcome")
    evidence = obj(result["evidence"])
    if task.phase == "verify":
        _vote(s, actor, task, outcome, evidence)
    else:
        require(
            outcome in {"reported", "fail", "unknown"}, "OUTCOME", "producer cannot verify itself"
        )
    mission.reserved -= task.cost
    mission.spent += task.cost
    task.funded = False
    task.status = "uncertain" if outcome == "unknown" else "finished"
    task.result = result
    if outcome in {"fail", "unknown"}:
        _residual(s, f"result:{tid}:{task.fence}", tid, outcome, "repair or reconcile")


def _vote(s: State, actor: str, task: Task, outcome: str, evidence: Object) -> None:
    fields(evidence, "source_digest contract verifier image behavior reason")
    require(outcome in {"pass", "fail", "unknown"}, "OUTCOME", "verification outcome required")
    cap = s.capabilities[task.subject]
    mission = s.missions[cap.mission]
    verifier = s.verifiers[mission.verifier]
    require(cap.status in {"candidate", "checked"}, "CAPABILITY", "subject is not live")
    require(
        evidence["source_digest"] == cap.source_digest
        and evidence["contract"] == mission.contract
        and evidence["verifier"] == mission.verifier
        and evidence["image"] == verifier.image,
        "BINDING",
        "verification scope substitution",
    )
    org = s.workers[actor].organization
    require(
        org not in cap.votes or cap.votes[org]["outcome"] == "unknown",
        "DUPLICATE",
        "organization already voted",
    )
    behavior = evidence["behavior"]
    if outcome == "pass" and verifier.exhaustive:
        behavior = text(behavior)
        require(
            len(behavior) == 64 and all(c in "0123456789abcdef" for c in behavior),
            "BINDING",
            "exhaustive behavior digest required",
        )
    else:
        require(behavior is None, "BINDING", "unsupported behavioral identity")
    text(evidence["reason"], limit=4096)
    cap.votes[org] = {"worker": actor, "outcome": outcome, "evidence": deepcopy(evidence)}
    if outcome == "fail":
        _invalidate(s, task.subject, "quarantined", "negative verification")
        return
    passing = [obj(v["evidence"])["behavior"] for v in cap.votes.values() if v["outcome"] == "pass"]
    if len(set(passing)) > 1:
        _invalidate(s, task.subject, "quarantined", "verification behavior mismatch")
        return
    if len(passing) >= 3:
        _eligible(s, cap.mission, cap.dependencies)
        cap.status = "checked"
        cap.behavior = str(passing[0]) if passing[0] is not None else ""


def _reconcile(s: State, actor: str, p: Object, ctx: Context) -> None:
    _admin(s, ctx)
    fields(p, "id retry reason")
    tid, reason = text(p["id"]), text(p["reason"], limit=4096)
    require(tid in s.tasks, "NOT_FOUND", "unknown task")
    task = s.tasks[tid]
    require(task.status == "uncertain", "STATE", "only uncertain tasks can be reconciled")
    require(type(p["retry"]) is bool, "SHAPE", "retry must be boolean")
    if p["retry"]:
        require(task.attempts < s.missions[task.mission].max_attempts, "ATTEMPTS", "retry limit")
        task.status = "ready"
    else:
        task.status = "abandoned"
    _residual(s, f"reconcile:{tid}:{task.fence}", tid, reason, "retain reconciliation history")


def _propose(s: State, actor: str, p: Object, ctx: Context) -> None:
    fields(p, "id task source expires origin")
    _worker(s, actor, "producer")
    cid, tid = text(p["id"]), text(p["task"])
    require(len(cid) <= 80 and len(s.capabilities) < 64, "LIMIT", "capability storage bound")
    require(cid not in s.capabilities and tid in s.tasks, "CONFLICT", "invalid candidate identity")
    task = s.tasks[tid]
    mission = _mission(s, task.mission, actor)
    require(
        task.status == "finished"
        and task.phase == "generate"
        and task.owner == actor
        and task.result.get("outcome") == "reported",
        "STATE",
        "completed generation required",
    )
    require(
        not any(c.task == tid for c in s.capabilities.values()),
        "DUPLICATE",
        "task already proposed",
    )
    require(mission.candidates < mission.max_candidates, "LIMIT", "candidate limit")
    require(len(s.tasks) + 4 <= 4096, "LIMIT", "verification task limit")
    _eligible(s, task.mission, task.dependencies)
    depth = 1 + max((s.capabilities[d].depth for d in task.dependencies), default=0)
    round_number = 1 + max((s.capabilities[d].round for d in task.dependencies), default=0)
    require(
        depth <= mission.max_depth and round_number <= mission.max_rounds,
        "LIMIT",
        "formation depth or round limit",
    )
    source = text(p["source"], limit=262144)
    require(len(source.encode("utf-8")) <= 262144, "LIMIT", "source byte limit")
    origin = text(p["origin"])
    require(origin in {"generated", "external"}, "ORIGIN", "invalid origin")
    expires = integer(p["expires"], low=s.height + 1, high=mission.expires)
    if task.dependencies:
        require(
            expires <= min(s.capabilities[d].expires for d in task.dependencies),
            "EXPIRED",
            "child may not outlive dependency",
        )
    require(
        4 * mission.verification_cost <= mission.budget - mission.spent - mission.reserved,
        "BUDGET",
        "verification cannot be funded",
    )
    digest = sha256(source.encode("utf-8")).hexdigest()
    require(
        obj(task.result["evidence"]).get("source_digest") == digest,
        "BINDING",
        "candidate differs from the completed generation receipt",
    )
    s.capabilities[cid] = Capability(
        task.mission,
        source,
        digest,
        tid,
        task.dependencies,
        round_number,
        depth,
        expires,
        origin=origin,
    )
    mission.candidates += 1
    mission.reserved += 4 * mission.verification_cost
    for org in sorted(s.organizations):
        vid = f"verify:{cid}:{org}"
        require(vid not in s.tasks, "CONFLICT", "verification task identity collision")
        s.tasks[vid] = Task(
            task.mission,
            "verify",
            {"capability": cid},
            (),
            mission.verification_cost,
            task.ttl,
            "isolated",
            org,
            cid,
            funded=True,
        )


def _revoke_capability(s: State, actor: str, p: Object, ctx: Context) -> None:
    _admin(s, ctx)
    fields(p, "id reason")
    cid = text(p["id"])
    require(cid in s.capabilities, "NOT_FOUND", "unknown capability")
    _invalidate(s, cid, "revoked", text(p["reason"], limit=4096))


def _resolve(s: State, actor: str, p: Object, ctx: Context) -> None:
    _admin(s, ctx)
    fields(p, "id evidence reason")
    rid, cid = text(p["id"]), text(p["evidence"])
    require(rid in s.residuals and cid in s.capabilities, "NOT_FOUND", "missing resolution records")
    require(s.capabilities[cid].status == "checked", "EVIDENCE", "checked repair evidence required")
    repair_task = s.tasks[s.capabilities[cid].task]
    require(
        rid in array(repair_task.spec.get("repairs", [])),
        "EVIDENCE",
        "repair evidence must name this residual",
    )
    residual = s.residuals[rid]
    require(residual.status == "open", "STATE", "residual already resolved")
    text(p["reason"], limit=4096)
    residual.status, residual.resolution = "resolved", cid


def accounting(state: State, mission_id: str) -> Object:
    require(mission_id in state.missions, "NOT_FOUND", "unknown mission")
    mission = state.missions[mission_id]
    caps = [c for c in state.capabilities.values() if c.mission == mission_id]
    live = [c for c in caps if c.status == "checked"]
    identities = {c.behavior for c in live if c.behavior and c.origin == "generated"}
    return {
        "mission": mission_id,
        "budget": mission.budget,
        "spent": mission.spent,
        "reserved": mission.reserved,
        "available": mission.budget - mission.spent - mission.reserved,
        "candidates": len(caps),
        "checked_artifacts": len(live),
        "unique_generated_behaviors": len(identities),
        "copies": sum(bool(c.behavior) and c.origin == "generated" for c in live) - len(identities),
        "external_artifacts": sum(c.origin == "external" for c in live),
        "unknown_novelty": sum(not c.behavior for c in live),
        "reuse_calls": mission.uses,
        "withdrawn_artifacts": sum(c.status in {"revoked", "expired", "quarantined"} for c in caps),
        "causal_acceleration": None,
        "reason": "No causal identification is performed.",
    }
