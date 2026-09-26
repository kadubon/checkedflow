"""Native v2 agent views and original-byte submission to one operator's validating node."""

from importlib.resources import files
from typing import Protocol

from checkedflow.agents.gateway import profile
from checkedflow.core.key_registry import roots
from checkedflow.core.operational import State, genesis
from checkedflow.core.values import Failure, Object, array, fields, obj, require, text
from checkedflow.core.work_acceptance import VERIFIER_COMMANDS, status
from checkedflow.core.work_tasks import WORKER_COMMANDS
from checkedflow.operational_codec import encode
from checkedflow.operational_identity import command_message
from checkedflow.operational_runtime import Runtime
from checkedflow.wire import digest, transaction_document


class Backend(Protocol):
    def live_state(self) -> State: ...
    def submit(self, raw: bytes) -> Object: ...


class Gateway:
    """No signer, administrative commands, automatic retransmission or code execution.

    Reads expose native v2 fields. A2A completion is a work receipt, not artifact acceptance.
    The configured backend must be the operator's validating node; this is not a light client.
    """

    def __init__(self, backend: Backend, chain: str, mission: str) -> None:
        self.backend = backend
        self.chain, self.mission = text(chain, limit=128), text(mission, limit=80)

    def state(self) -> State:
        try:
            state = self.backend.live_state()
        except Failure:
            raise
        except Exception as exc:
            raise Failure("UNAVAILABLE", "committed state unavailable") from exc
        require(state.profile == "checkedflow/control-state/v2", "VERSION", "v2 state required")
        require(state.chain == self.chain, "CHAIN", "gateway chain mismatch")
        require(state.mission == self.mission, "SCOPE", "gateway mission mismatch")
        return state

    def journal_binding(self) -> str:
        state = self.state()
        initial = genesis(
            state.chain,
            state.mission,
            state.organizations,
            roots(state.credentials),
            limits=state.journal.limits,
        )
        return digest(
            {"profile": "checkedflow-agent-journal/v2", "origin": digest(encode(initial))}
        )

    def snapshot(self) -> Object:
        state = self.state()
        encoded = encode(state)
        common: Object = {
            "chain": state.chain,
            "mission": state.mission,
            "height": state.height,
            "state_hash": digest(encoded),
            "profile": state.profile,
        }
        candidates: Object = {}
        for candidate, record in zip(state.candidates, array(encoded["candidates"]), strict=True):
            candidates[candidate.identity] = common | {
                "record": obj(record)
                | {"status": status(candidate, state.credentials, state.height)}
            }
        tasks: Object = {}
        for task, record in zip(state.tasks, array(encoded["tasks"]), strict=True):
            related: Object = {
                candidate.identity: obj(obj(candidates[candidate.identity])["record"])
                for candidate in state.candidates
                if task.identity in candidate.checks
            }
            tasks[task.identity] = common | {"record": record, "capabilities": related}
        mission = common | {
            "record": {"mode": state.mode, "epoch": state.journal.epoch},
            "accounting": obj(encoded["budget"])
            | {
                "spent": state.budget.spent,
                "reserved": state.budget.reserved,
                "available": state.budget.available,
                "protected_verification": state.budget.protected_verification,
                "basis": "modeled budget; not observed metering",
            },
            "nonces": dict(state.journal.actors),
        }
        return {"mission": mission, "tasks": tasks, "capabilities": candidates, "residuals": {}}

    def inspect(self, kind: str = "mission", identity: str = "") -> Object:
        snapshot = self.snapshot()
        if kind == "mission":
            return obj(snapshot["mission"])
        if kind == "tasks":
            mission = obj(snapshot["mission"])
            return {
                key: mission[key] for key in ("chain", "mission", "height", "state_hash", "profile")
            } | {
                "records": {
                    key: obj(value)["record"] for key, value in obj(snapshot["tasks"]).items()
                }
            }
        require(kind in {"task", "capability", "residual"}, "SHAPE", "unknown inspection kind")
        collection = {"task": "tasks", "capability": "capabilities", "residual": "residuals"}[kind]
        records = obj(snapshot[collection])
        require(identity in records, "NOT_FOUND", "record unavailable in this mission")
        return obj(records[identity])

    def command(self, envelope_json: str) -> Object:
        try:
            raw = envelope_json.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise Failure("ENCODING", "signed JSON must be valid UTF-8") from exc
        parsed = transaction_document(raw)
        fields(parsed, "command signatures")
        command = obj(parsed["command"])
        command_message(command)
        return command

    def task_target(self, command: Object) -> str:
        require(command["kind"] in WORKER_COMMANDS, "SCOPE", "worker task command required")
        return text(obj(command["payload"])["task"])

    def cancellation(self, command: Object, identity: str) -> None:
        raise Failure("SCOPE", "v2 unknowns cannot be cancelled through an agent transport")

    def submit(self, envelope_json: str, *, message_id: str = "") -> Object:
        command = self.command(envelope_json)
        identity = text(command["id"])
        require(not message_id or message_id == identity, "ID", "message ID must equal command ID")
        require(
            command["kind"] in WORKER_COMMANDS | VERIFIER_COMMANDS,
            "SCOPE",
            "administration unavailable through agent transports",
        )
        require(obj(command["payload"]).get("mission") == self.mission, "SCOPE", "mission mismatch")
        state = self.state()
        raw = envelope_json.encode("utf-8")
        Runtime(state).apply(raw, height=state.height + 1)
        expected = digest(command)

        def confirmed(value: State) -> bool:
            return any(
                r.request == identity and r.command_digest == expected
                for r in value.journal.receipts
            )

        if not confirmed(state):
            try:
                self.backend.submit(raw)
                state = self.state()
            except Failure as exc:
                if exc.code == "REJECTED":
                    raise
                raise Failure("OUTCOME_UNKNOWN", "query original command before retrying") from exc
            except Exception as exc:
                raise Failure("OUTCOME_UNKNOWN", "query original command before retrying") from exc
        require(confirmed(state), "OUTCOME_UNKNOWN", "committed receipt not observed")
        return {
            "command_id": identity,
            "command_digest": expected,
            "chain": self.chain,
            "mission": self.mission,
            "height": state.height,
            "state_hash": digest(encode(state)),
            "status": "committed",
            "verification": "not_implied",
            "protocol": "checkedflow/v2",
        }

    def transport_profile(self) -> Object:
        value = profile()
        value["profile"], value["command_protocol"] = "checkedflow-agents/v2", "checkedflow/v2"
        value["signed_transport"] = "original UTF-8 JSON bytes; no v1 translation"
        value["submit_kinds"] = list(sorted(WORKER_COMMANDS | VERIFIER_COMMANDS))
        value["native_records"] = (
            "v2 task, candidate and budget fields; no synthetic residual graph"
        )
        obj(value["a2a"])["cancellation"] = "unsupported for v2 unknown work; preserve obligations"
        return value

    def envelope_schema(self) -> str:
        return files("checkedflow").joinpath("data/operational-envelope.schema.json").read_text()
