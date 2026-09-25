"""Typed snapshots for the local trusted store; portable replay uses signed commands."""

import json
from dataclasses import asdict
from dataclasses import fields as data_fields
from typing import cast

from checkedflow.core.model import Capability, Mission, Residual, State, Task, Verifier, Worker
from checkedflow.core.values import JSON, Object, array, fields, integer, names, obj, require, text
from checkedflow.wire import validate


def encode(state: State) -> Object:
    return obj(validate(json.loads(json.dumps(asdict(state)))))


def _str(value: JSON) -> str:
    require(isinstance(value, str), "SHAPE", "string required")
    return cast(str, value)


def _bool(value: JSON) -> bool:
    require(type(value) is bool, "SHAPE", "boolean required")
    return cast(bool, value)


def decode(value: Object) -> State:
    fields(value, " ".join(f.name for f in data_fields(State)))
    state = State(
        text(value["chain"]),
        {k: text(v) for k, v in obj(value["organizations"]).items()},
        integer(value["height"]),
    )
    for key, raw in obj(value["workers"]).items():
        r = obj(raw)
        fields(r, "key organization roles revoked")
        state.workers[key] = Worker(
            text(r["key"]), text(r["organization"]), names(r["roles"]), _bool(r["revoked"])
        )
    for key, raw in obj(value["verifiers"]).items():
        r = obj(raw)
        fields(r, "contract image argv exhaustive")
        state.verifiers[key] = Verifier(
            text(r["contract"]),
            text(r["image"]),
            tuple(text(v) for v in array(r["argv"], limit=32)),
            _bool(r["exhaustive"]),
        )
    for key, raw in obj(value["missions"]).items():
        r = obj(raw)
        fields(r, " ".join(f.name for f in data_fields(Mission)))
        state.missions[key] = Mission(
            workers=names(r["workers"]),
            budget=integer(r["budget"]),
            verification_reserve=integer(r["verification_reserve"]),
            verification_cost=integer(r["verification_cost"]),
            expires=integer(r["expires"]),
            max_rounds=integer(r["max_rounds"]),
            max_candidates=integer(r["max_candidates"]),
            max_depth=integer(r["max_depth"]),
            max_attempts=integer(r["max_attempts"]),
            verifier=text(r["verifier"]),
            receiver=text(r["receiver"]),
            contract=text(r["contract"]),
            image=text(r["image"]),
            spent=integer(r["spent"]),
            reserved=integer(r["reserved"]),
            candidates=integer(r["candidates"]),
            uses=integer(r["uses"]),
        )
    for key, raw in obj(value["tasks"]).items():
        r = obj(raw)
        fields(r, " ".join(f.name for f in data_fields(Task)))
        state.tasks[key] = Task(
            mission=text(r["mission"]),
            phase=text(r["phase"]),
            spec=obj(r["spec"]),
            dependencies=names(r["dependencies"]),
            cost=integer(r["cost"]),
            ttl=integer(r["ttl"]),
            effect=text(r["effect"]),
            organization=_str(r["organization"]),
            subject=_str(r["subject"]),
            status=text(r["status"]),
            owner=_str(r["owner"]),
            fence=integer(r["fence"]),
            deadline=integer(r["deadline"]),
            attempts=integer(r["attempts"]),
            funded=_bool(r["funded"]),
            result=obj(r["result"]),
        )
    for key, raw in obj(value["capabilities"]).items():
        r = obj(raw)
        fields(r, " ".join(f.name for f in data_fields(Capability)))
        state.capabilities[key] = Capability(
            mission=text(r["mission"]),
            source=text(r["source"], limit=262144),
            source_digest=text(r["source_digest"]),
            task=text(r["task"]),
            dependencies=names(r["dependencies"]),
            round=integer(r["round"]),
            depth=integer(r["depth"]),
            expires=integer(r["expires"]),
            status=text(r["status"]),
            behavior=_str(r["behavior"]),
            votes={k: obj(v) for k, v in obj(r["votes"]).items()},
            origin=text(r["origin"]),
        )
    for key, raw in obj(value["residuals"]).items():
        r = obj(raw)
        fields(r, "subject reason trigger status resolution")
        state.residuals[key] = Residual(
            text(r["subject"]),
            text(r["reason"], limit=4096),
            text(r["trigger"]),
            text(r["status"]),
            _str(r["resolution"]),
        )
    state.processed = {k: text(v) for k, v in obj(value["processed"]).items()}
    state.nonces = {k: integer(v) for k, v in obj(value["nonces"]).items()}
    return state
