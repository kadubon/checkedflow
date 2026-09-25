"""Mission-scoped access to one operator's committed node, without signing keys."""

from importlib.resources import files
from typing import Protocol

from checkedflow.core.machine import accounting, transition
from checkedflow.core.model import State
from checkedflow.core.values import Failure, Object, obj, require, text
from checkedflow.identity import authenticate
from checkedflow.serialization import encode
from checkedflow.wire import digest, document, transaction_document


class Backend(Protocol):
    def state(self) -> State: ...

    def submit(self, envelope: Object) -> Object: ...


def profile() -> Object:
    return document(files("checkedflow").joinpath("data/agents.json").read_bytes())


class Gateway:
    """Validate locally, submit once, and observe committed state on every read.

    The backend must be an operator-controlled validating node. A dry run predicts
    admission at the next height; only consensus decides the actual outcome.
    Transport errors after submission are ambiguous and never trigger a retry.
    """

    def __init__(self, backend: Backend, chain: str, mission: str) -> None:
        self.backend, self.chain, self.mission = backend, chain, mission

    def state(self) -> State:
        try:
            state = self.backend.state()
        except Failure:
            raise
        except Exception as exc:
            raise Failure("UNAVAILABLE", "committed state could not be read") from exc
        require(state.chain == self.chain, "CHAIN", "gateway chain mismatch")
        require(self.mission in state.missions, "SCOPE", "gateway mission is unavailable")
        return state

    def _task(self, state: State, identity: str) -> None:
        require(
            identity in state.tasks and state.tasks[identity].mission == self.mission,
            "NOT_FOUND",
            "task is not visible in this mission",
        )

    def _capability(self, state: State, identity: str) -> None:
        require(
            identity in state.capabilities and state.capabilities[identity].mission == self.mission,
            "NOT_FOUND",
            "capability is not visible in this mission",
        )

    def _residual(self, state: State, identity: str) -> None:
        # A subject can name either kind of record. IDs are not a shared namespace.
        require(identity in state.residuals, "NOT_FOUND", "residual is not visible")
        subject = state.residuals[identity].subject
        task = state.tasks.get(subject)
        cap = state.capabilities.get(subject)
        require(
            (task is not None or cap is not None)
            and (task is None or task.mission == self.mission)
            and (cap is None or cap.mission == self.mission),
            "NOT_FOUND",
            "residual scope is absent or ambiguous",
        )

    def inspect(self, kind: str = "mission", identity: str = "") -> Object:
        state = self.state()
        encoded = encode(state)
        result: Object = {
            "chain": state.chain,
            "mission": self.mission,
            "height": state.height,
            "state_hash": digest(encoded),
        }
        if kind == "mission":
            result["record"] = obj(encoded["missions"])[self.mission]
            result["accounting"] = accounting(state, self.mission)
            result["nonces"] = {
                actor: state.nonces.get(actor, 0)
                for actor in (*state.missions[self.mission].workers, *state.organizations)
            }
        elif kind == "tasks":
            result["records"] = {
                key: obj(encoded["tasks"])[key]
                for key in sorted(state.tasks)
                if state.tasks[key].mission == self.mission
            }
        elif kind == "task":
            self._task(state, identity)
            result["record"] = obj(encoded["tasks"])[identity]
            result["capabilities"] = {
                key: {"status": cap.status, "source_digest": cap.source_digest}
                for key, cap in state.capabilities.items()
                if cap.task == identity and cap.mission == self.mission
            }
        elif kind == "capability":
            self._capability(state, identity)
            result["record"] = obj(encoded["capabilities"])[identity]
        elif kind == "residual":
            self._residual(state, identity)
            result["record"] = obj(encoded["residuals"])[identity]
        else:
            raise Failure("SHAPE", "unknown inspection kind")
        return result

    def submit(self, envelope_json: str, *, message_id: str = "") -> Object:
        try:
            raw = envelope_json.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise Failure("ENCODING", "signed JSON must be valid UTF-8") from exc
        envelope = transaction_document(raw)
        state = self.state()
        command, context = authenticate(state, envelope, state.height + 1)
        kind, payload = text(command["kind"]), obj(command["payload"])
        identity = text(command["id"])
        require(not message_id or message_id == identity, "ID", "message ID must equal command ID")
        if kind == "task.create":
            require(payload["mission"] == self.mission, "SCOPE", "mission mismatch")
        elif kind in {
            "task.lease",
            "task.start",
            "task.heartbeat",
            "task.finish",
            "task.reconcile",
        }:
            self._task(state, text(payload["id"]))
        elif kind == "capability.propose":
            self._task(state, text(payload["task"]))
        elif kind == "capability.revoke":
            self._capability(state, text(payload["id"]))
        elif kind == "residual.resolve":
            self._residual(state, text(payload["id"]))
        else:
            raise Failure("SCOPE", "global governance is not available through agent transports")
        transition(state, command, context)
        confirmed = state
        # A committed duplicate need not enter CometBFT's transaction cache again.
        # Authentication, scope and digest-conflict checks above still apply.
        if identity not in state.processed:
            try:
                self.backend.submit(envelope)
            except Failure as exc:
                if exc.code == "REJECTED":
                    raise
                raise Failure("OUTCOME_UNKNOWN", "query committed state before retrying") from exc
            except Exception as exc:
                raise Failure("OUTCOME_UNKNOWN", "query committed state before retrying") from exc
            try:
                confirmed = self.state()
            except Failure as exc:
                raise Failure(
                    "OUTCOME_UNKNOWN", "commit observation unavailable; query before retrying"
                ) from exc
        # A successful RPC response alone is insufficient; require the committed digest.
        require(
            confirmed.processed.get(identity) == context.command_digest,
            "OUTCOME_UNKNOWN",
            "command not observed in committed state; query before retrying",
        )
        return {
            "command_id": identity,
            "command_digest": context.command_digest,
            "chain": self.chain,
            "mission": self.mission,
            "height": confirmed.height,
            "state_hash": digest(encode(confirmed)),
            "status": "committed",
            "verification": "not_implied",
        }
