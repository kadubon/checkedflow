"""A2A server semantics over authenticated, mission-scoped committed state."""

import secrets
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path

import anyio
import httpx
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import a2a_pb2 as pb
from a2a.utils.errors import (
    InternalError,
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
)
from google.protobuf.json_format import MessageToDict, ParseDict
from starlette.types import ASGIApp

from checkedflow import __version__
from checkedflow.agents.access import Policy, Principal
from checkedflow.agents.gateway import AgentGateway as Gateway
from checkedflow.agents.http import MAX_BODY as MAX_BODY
from checkedflow.agents.journal import Journal
from checkedflow.agents.oauth import OAuth
from checkedflow.agents.push import Push
from checkedflow.agents.secrets import Keyring, public_configuration
from checkedflow.core.values import Failure, Object, fields, obj, require, text
from checkedflow.wire import digest, dumps

TERMINAL = {
    pb.TASK_STATE_COMPLETED,
    pb.TASK_STATE_FAILED,
    pb.TASK_STATE_CANCELED,
    pb.TASK_STATE_REJECTED,
}
INTERRUPTED = {pb.TASK_STATE_INPUT_REQUIRED, pb.TASK_STATE_AUTH_REQUIRED}


@contextmanager
def errors() -> Iterator[None]:
    try:
        yield
    except Failure as exc:
        if exc.code == "NOT_FOUND":
            raise TaskNotFoundError() from exc
        error = (
            InternalError
            if exc.code in {"OUTCOME_UNKNOWN", "UNAVAILABLE", "RPC", "STORAGE"}
            else InvalidParamsError
        )
        raise error(message=str(exc), data={"checkedflowCode": exc.code}) from exc
    except ValueError as exc:
        raise InvalidParamsError(message="Invalid input") from exc


def card(url: str, grpc_url: str = "") -> pb.AgentCard:
    interfaces = [
        {"url": url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
        {
            "url": url.removesuffix("/rpc") + "/v1",
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        },
    ]
    if grpc_url:
        interfaces.append({"url": grpc_url, "protocolBinding": "GRPC", "protocolVersion": "1.0"})
    return ParseDict(
        {
            "name": "CheckedFlow",
            "version": __version__,
            "description": "Coordinate signed work, evidence and conditional reuse in one mission.",
            "supportedInterfaces": interfaces,
            "capabilities": {
                "streaming": True,
                "pushNotifications": True,
                "extendedAgentCard": True,
            },
            "securitySchemes": {"operatorBearer": {"httpAuthSecurityScheme": {"scheme": "bearer"}}},
            "securityRequirements": [{"schemes": {"operatorBearer": {"list": []}}}],
            "defaultInputModes": ["application/json"],
            "defaultOutputModes": ["application/json"],
            "skills": [
                {
                    "id": "checkedflow-command",
                    "name": "Signed mission commands",
                    "description": "operation=submit returns a commit acknowledgment; "
                    "operation=task tracks work. Both require envelopeJson with exact signed "
                    "JSON and messageId=command.id. A token never authorizes work.",
                    "tags": ["verification", "signed-command", "task", "budget"],
                },
                {
                    "id": "checkedflow-inspect",
                    "name": "Inspect committed evidence",
                    "description": "operation=profile discovers the contract. operation=inspect "
                    "takes kind=mission|tasks|task|capability|residual and identity. "
                    "Task operations include history, artifacts, streams and notifications.",
                    "tags": ["read-only", "evidence", "reuse"],
                },
            ],
        },
        pb.AgentCard(),
    )


def task_projection(identity: str, value: Object) -> pb.Task:
    record = obj(value["record"])
    states = {
        "ready": pb.TASK_STATE_SUBMITTED,
        "leased": pb.TASK_STATE_WORKING,
        "running": pb.TASK_STATE_WORKING,
        "uncertain": pb.TASK_STATE_INPUT_REQUIRED,
        "unknown": pb.TASK_STATE_INPUT_REQUIRED,
        "cancelled": pb.TASK_STATE_CANCELED,
        "abandoned": pb.TASK_STATE_CANCELED,
        "finished": pb.TASK_STATE_FAILED
        if obj(record.get("result", {})).get("outcome") == "fail"
        else pb.TASK_STATE_COMPLETED,
    }
    task = pb.Task(
        id=identity,
        context_id=text(value["mission"]),
        status=pb.TaskStatus(state=states[text(record["status"])]),
        metadata=ParseDict(
            {
                "checkedflowJson": dumps(value, string_limit=16777216).decode(),
                "completionMeaning": "work receipt; artifact acceptance is a separate status",
            },
            pb.Task().metadata,
        ),
    )
    receipt = obj(record.get("result", {}))
    if value.get("profile") == "checkedflow/control-state/v2" and record.get("evidence"):
        receipt = {"evidence_digest": record["evidence"], "availability": "not_implied"}
    if receipt:
        task.artifacts.add(
            artifact_id="receipt:" + identity,
            name="Committed work receipt",
            description="Untrusted evidence; completion does not imply acceptance.",
            parts=[ParseDict({"data": {"receiptJson": dumps(receipt).decode()}}, pb.Part())],
        )
    return task


class Handler(RequestHandler):
    def __init__(
        self,
        gateway: Gateway,
        *,
        journal: Journal | None = None,
        agent_card: pb.AgentCard | None = None,
        push: Push | None = None,
        policy: Policy | None = None,
    ) -> None:
        self.gateway = gateway
        self.policy = policy
        require(
            policy is None or (policy.chain == gateway.chain and policy.mission == gateway.mission),
            "ACCESS",
            "policy must match gateway",
        )
        self.journal = journal or Journal(gateway)
        self.agent_card = agent_card or card("http://127.0.0.1:8080/rpc")
        self.push = push or Push()
        # SDKs may read the first stream item in a different task from later items.
        self.streams = anyio.Semaphore(128)

    def authorize(self, context: ServerCallContext | None) -> Principal | None:
        if self.policy is None:
            return None
        owner = context.state.get("checkedflow.principal") if context is not None else None
        if not isinstance(owner, Principal):
            raise Failure("ACCESS", "authenticated client required")
        self.policy.check(owner)
        return owner

    def access_binding(self, context: ServerCallContext) -> Object:
        owner = self.authorize(context)
        if self.policy is None or owner is None:
            return {}
        return {"client": owner.identity, "policy": self.policy.check(owner)}

    def owned(self, value: Object, owner: Principal | None) -> bool:
        if self.policy is None:
            return True
        if owner is None or "_principal" not in value:
            return False
        return Principal.restore(obj(value["_principal"])).identity == owner.identity

    def scope(self, tenant: str) -> None:
        require(
            not tenant or tenant == self.gateway.mission, "SCOPE", "tenant must match this mission"
        )

    async def refresh(self) -> None:
        await anyio.to_thread.run_sync(self.journal.refresh)

    def project(self, identity: str, history: int = 0) -> pb.Task:
        require(history >= 0, "SHAPE", "historyLength must be non-negative")
        history = min(history, 64)
        value, stamp, entries, _ = self.journal.get(identity)
        task = task_projection(identity, value)
        task.status.timestamp.FromNanoseconds(stamp)
        if history:
            for index, entry in enumerate(entries[-history:]):
                task.history.add(
                    message_id=f"observation:{identity}:{stamp}:{index}",
                    task_id=identity,
                    context_id=self.gateway.mission,
                    role=pb.ROLE_AGENT,
                    parts=[
                        ParseDict({"data": {"observationJson": dumps(entry).decode()}}, pb.Part())
                    ],
                )
        return task

    async def on_get_task(self, params: pb.GetTaskRequest, context: ServerCallContext) -> pb.Task:
        with errors():
            self.authorize(context)
            self.scope(params.tenant)
            await self.refresh()
            return self.project(params.id, params.history_length)

    async def on_list_tasks(
        self, params: pb.ListTasksRequest, context: ServerCallContext
    ) -> pb.ListTasksResponse:
        with errors():
            self.authorize(context)
            self.scope(params.tenant)
            require(
                not params.HasField("page_size") or 1 <= params.page_size <= 100,
                "SHAPE",
                "pageSize must be between 1 and 100",
            )
            require(params.history_length >= 0, "SHAPE", "historyLength must be non-negative")
            require(params.status in pb.TaskState.values(), "SHAPE", "unknown task state")
            await self.refresh()
            size = params.page_size or 50
            after = (
                params.status_timestamp_after.ToNanoseconds()
                if params.HasField("status_timestamp_after")
                else 0
            )
            tasks = [self.project(i, params.history_length) for i in self.journal.ordered()]
            tasks = [
                t
                for t in tasks
                if (not params.context_id or t.context_id == params.context_id)
                and (not params.status or t.status.state == params.status)
                and t.status.timestamp.ToNanoseconds() >= after
            ]
            filters = digest(
                {
                    "access": self.access_binding(context),
                    "context": params.context_id,
                    "status": params.status,
                    "after": str(after),
                    "size": size,
                    "history": params.history_length,
                    "artifacts": params.include_artifacts,
                }
            )
            revision = digest(
                {
                    "tasks": [
                        {"id": t.id, "stamp": str(t.status.timestamp.ToNanoseconds())}
                        for t in tasks
                    ]
                }
            )
            offset = self.page_offset(
                params.page_token, {"filters": filters, "revision": revision}, len(tasks)
            )
            page = tasks[offset : offset + size]
            if not params.include_artifacts:
                for task in page:
                    task.ClearField("artifacts")
            token = self.next_page(
                {"filters": filters, "revision": revision}, offset, size, len(tasks)
            )
            return pb.ListTasksResponse(
                tasks=page, next_page_token=token, page_size=size, total_size=len(tasks)
            )

    def page_offset(self, token: str, binding: Object, total: int) -> int:
        if not token:
            return 0
        cursor = self.journal.read_cursor(token)
        require(
            cursor.get("binding") == binding,
            "CURSOR",
            "listing changed or filters differ; restart pagination",
        )
        offset = int(text(cursor.get("offset")))
        require(0 <= offset <= total, "CURSOR", "invalid offset")
        return offset

    def next_page(self, binding: Object, offset: int, size: int, total: int) -> str:
        return (
            self.journal.cursor({"binding": binding, "offset": str(offset + size)})
            if offset + size < total
            else ""
        )

    async def dispatch(
        self, params: pb.SendMessageRequest, context: ServerCallContext
    ) -> pb.Message | pb.Task:
        self.authorize(context)
        message = params.message
        self.scope(params.tenant)
        require(
            message.role == pb.ROLE_USER and bool(message.message_id),
            "SHAPE",
            "user role and messageId required",
        )
        require(
            not message.context_id or message.context_id == self.gateway.mission,
            "SCOPE",
            "contextId must match mission",
        )
        require(not message.extensions, "SHAPE", "no extensions are advertised")
        require(
            len(message.parts) == 1 and message.parts[0].HasField("data"),
            "SHAPE",
            "one JSON data part required",
        )
        require(
            not params.configuration.accepted_output_modes
            or "application/json" in params.configuration.accepted_output_modes,
            "SHAPE",
            "application/json output must be accepted",
        )
        require(params.configuration.history_length >= 0, "SHAPE", "historyLength bound")
        for identity in message.reference_task_ids:
            await anyio.to_thread.run_sync(self.gateway.inspect, "task", identity)
        data = obj(MessageToDict(message.parts[0].data))
        operation = text(data.get("operation"))
        tracked = ""
        if operation == "profile":
            fields(data, "operation")
            value = self.gateway.transport_profile()
        elif operation == "inspect":
            fields(data, "operation kind identity")
            require(
                isinstance(data["identity"], str) and len(data["identity"]) <= 256,
                "SHAPE",
                "invalid identity",
            )
            value = await anyio.to_thread.run_sync(
                self.gateway.inspect, text(data["kind"]), str(data["identity"])
            )
        elif operation in {"submit", "task"}:
            fields(data, "operation envelopeJson")
            envelope = text(data["envelopeJson"], limit=1048576)
            command = self.gateway.command(envelope)
            if self.policy is not None:
                self.policy.command(self.authorize(context), command)
            if operation == "task":
                tracked = self.gateway.task_target(command)
            require(
                not message.task_id or message.task_id == tracked,
                "BINDING",
                "taskId must match tracked signed work",
            )
            if params.configuration.HasField("task_push_notification_config"):
                require(bool(tracked), "SHAPE", "push configuration requires operation=task")
                config = params.configuration.task_push_notification_config
                require(
                    not config.task_id or config.task_id == tracked, "BINDING", "push task mismatch"
                )
                self.scope(config.tenant)
                require(len(config.id) <= 256, "SHAPE", "configuration ID limit")
                self.push.validate(obj(MessageToDict(config)))
            value = await anyio.to_thread.run_sync(
                lambda: self.gateway.submit(envelope, message_id=message.message_id)
            )
        else:
            raise Failure("SHAPE", "unknown operation")
        if tracked:
            try:
                await self.refresh()
                if params.configuration.HasField("task_push_notification_config"):
                    config = pb.TaskPushNotificationConfig()
                    config.CopyFrom(params.configuration.task_push_notification_config)
                    config.task_id = tracked
                    self.store_config(config, context)
                return self.project(tracked, params.configuration.history_length)
            except Failure as exc:
                raise Failure(
                    "OUTCOME_UNKNOWN", "command committed; query task and callback state"
                ) from exc
        require(
            not message.task_id
            and not params.configuration.HasField("task_push_notification_config"),
            "SHAPE",
            "direct messages cannot attach task state",
        )
        return pb.Message(
            message_id=f"reply:{message.message_id}",
            context_id=self.gateway.mission,
            role=pb.ROLE_AGENT,
            parts=[
                ParseDict(
                    {"data": {"checkedflowJson": dumps(value, string_limit=16777216).decode()}},
                    pb.Part(),
                )
            ],
        )

    async def on_message_send(
        self, params: pb.SendMessageRequest, context: ServerCallContext
    ) -> pb.Message | pb.Task:
        with errors():
            self.authorize(context)
            value = await self.dispatch(params, context)
            if isinstance(value, pb.Task) and not params.configuration.return_immediately:
                async with self.streams:
                    while value.status.state not in TERMINAL | INTERRUPTED:
                        await anyio.sleep(0.25)
                        self.authorize(context)
                        await self.refresh()
                        value = self.project(value.id, params.configuration.history_length)
            return value

    async def updates(self, task: pb.Task, context: ServerCallContext) -> AsyncGenerator[Event]:
        async with self.streams:
            self.authorize(context)
            yield task
            while task.status.state not in TERMINAL | INTERRUPTED:
                previous = task.SerializeToString(deterministic=True)
                await anyio.sleep(0.25)
                self.authorize(context)
                await self.refresh()
                current = self.project(task.id)
                if current.SerializeToString(deterministic=True) == previous:
                    continue
                if current.artifacts != task.artifacts:
                    for artifact in current.artifacts:
                        yield pb.TaskArtifactUpdateEvent(
                            task_id=task.id,
                            context_id=task.context_id,
                            artifact=artifact,
                            append=False,
                            last_chunk=True,
                        )
                task = current
                yield pb.TaskStatusUpdateEvent(
                    task_id=task.id,
                    context_id=task.context_id,
                    status=task.status,
                    metadata=task.metadata,
                )

    async def on_message_send_stream(
        self, params: pb.SendMessageRequest, context: ServerCallContext
    ) -> AsyncGenerator[Event]:
        with errors():
            self.authorize(context)
            value = await self.dispatch(params, context)
            if isinstance(value, pb.Message):
                yield value
            else:
                async for event in self.updates(value, context):
                    yield event

    async def on_subscribe_to_task(
        self, params: pb.SubscribeToTaskRequest, context: ServerCallContext
    ) -> AsyncGenerator[Event]:
        task = await self.on_get_task(
            pb.GetTaskRequest(id=params.id, tenant=params.tenant), context
        )
        if task.status.state in TERMINAL:
            raise InvalidParamsError(message="Cannot subscribe to a terminal task; use GetTask")
        with errors():
            self.authorize(context)
            async for event in self.updates(task, context):
                yield event

    async def on_cancel_task(
        self, params: pb.CancelTaskRequest, context: ServerCallContext
    ) -> pb.Task:
        task = await self.on_get_task(
            pb.GetTaskRequest(id=params.id, tenant=params.tenant), context
        )
        with errors():
            self.authorize(context)
            data = obj(MessageToDict(params.metadata))
            if task.status.state != pb.TASK_STATE_INPUT_REQUIRED or "envelopeJson" not in data:
                raise TaskNotCancelableError(
                    message="Cancellation requires uncertain work and three-organization "
                    "signed task.reconcile (retry=false)"
                )
            envelope = text(data["envelopeJson"], limit=1048576)
            command = self.gateway.command(envelope)
            if self.policy is not None:
                self.policy.command(self.authorize(context), command)
            self.gateway.cancellation(command, params.id)
            await anyio.to_thread.run_sync(self.gateway.submit, envelope)
            await self.refresh()
            return self.project(params.id)

    def store_config(
        self, params: pb.TaskPushNotificationConfig, context: ServerCallContext | None = None
    ) -> pb.TaskPushNotificationConfig:
        owner = self.authorize(context)
        self.scope(params.tenant)
        self.journal.get(params.task_id)
        config = pb.TaskPushNotificationConfig()
        config.CopyFrom(params)
        config.id = config.id or secrets.token_hex(16)
        require(len(config.id) <= 256, "SHAPE", "configuration ID limit")
        value = obj(MessageToDict(config))
        self.push.validate(value)
        with self.journal.lock:
            existing = [
                v for v in self.journal.configurations(config.task_id) if v.get("id") == config.id
            ]
            require(
                not existing or self.owned(existing[0], owner),
                "NOT_FOUND",
                "configuration unavailable",
            )
            if owner is not None:
                value["_principal"] = owner.record()
            self.journal.put_config(config.task_id, config.id, value)
        return ParseDict(public_configuration(value), pb.TaskPushNotificationConfig())

    async def on_create_task_push_notification_config(
        self, params: pb.TaskPushNotificationConfig, context: ServerCallContext
    ) -> pb.TaskPushNotificationConfig:
        with errors():
            self.authorize(context)
            await self.refresh()
            return self.store_config(params, context)

    async def on_get_task_push_notification_config(
        self, params: pb.GetTaskPushNotificationConfigRequest, context: ServerCallContext
    ) -> pb.TaskPushNotificationConfig:
        with errors():
            self.authorize(context)
            self.scope(params.tenant)
            await self.refresh()
            self.journal.get(params.task_id)
            matches = [
                v
                for v in self.journal.configurations(params.task_id)
                if v.get("id") == params.id and self.owned(v, self.authorize(context))
            ]
            require(bool(matches), "NOT_FOUND", "push configuration not found")
            return ParseDict(public_configuration(matches[0]), pb.TaskPushNotificationConfig())

    async def on_list_task_push_notification_configs(
        self, params: pb.ListTaskPushNotificationConfigsRequest, context: ServerCallContext
    ) -> pb.ListTaskPushNotificationConfigsResponse:
        with errors():
            self.authorize(context)
            self.scope(params.tenant)
            await self.refresh()
            self.journal.get(params.task_id)
            require(
                0 <= params.page_size <= 100,
                "SHAPE",
                "pageSize bound",
            )
            values, revision = self.journal.configuration_snapshot(params.task_id)
            values = [v for v in values if self.owned(v, self.authorize(context))]
            size = params.page_size or 50
            binding: Object = {
                "access": self.access_binding(context),
                "task": params.task_id,
                "revision": revision,
                "size": size,
            }
            offset = self.page_offset(params.page_token, binding, len(values))
            return pb.ListTaskPushNotificationConfigsResponse(
                configs=[
                    ParseDict(public_configuration(v), pb.TaskPushNotificationConfig())
                    for v in values[offset : offset + size]
                ],
                next_page_token=self.next_page(binding, offset, size, len(values)),
            )

    async def on_delete_task_push_notification_config(
        self, params: pb.DeleteTaskPushNotificationConfigRequest, context: ServerCallContext
    ) -> None:
        with errors():
            self.authorize(context)
            self.scope(params.tenant)
            await self.refresh()
            self.journal.get(params.task_id)
            with self.journal.lock:
                values = [
                    v
                    for v in self.journal.configurations(params.task_id)
                    if v.get("id") == params.id and self.owned(v, self.authorize(context))
                ]
                require(bool(values), "NOT_FOUND", "configuration unavailable")
                self.journal.delete_config(params.task_id, params.id)

    async def on_get_extended_agent_card(
        self, params: pb.GetExtendedAgentCardRequest, context: ServerCallContext
    ) -> pb.AgentCard:
        with errors():
            self.authorize(context)
            self.scope(params.tenant)
            await anyio.to_thread.run_sync(self.gateway.state)
            extended = pb.AgentCard()
            extended.CopyFrom(self.agent_card)
            extended.description += f" Authenticated mission: {self.gateway.mission}."
            return extended

    async def monitor(self) -> None:
        """Coalesce updates; persist acknowledgments and cap failed delivery attempts at three."""
        while True:
            try:
                await self.refresh()
                for task, identity, config, fingerprint, attempts in self.journal.pending():
                    if attempts >= 3:
                        continue
                    success = False
                    try:
                        if self.policy is not None:
                            self.policy.check(Principal.restore(obj(config.get("_principal"))))
                        with anyio.fail_after(10):
                            projected = obj(MessageToDict(self.project(task)))
                            if self.policy is None:
                                success = await self.push.deliver(config, projected)
                            else:
                                policy = self.policy
                                owner = Principal.restore(obj(config.get("_principal")))
                                success = await self.push.deliver(
                                    config, projected, authorize=partial(policy.check, owner)
                                )
                    except (Failure, OSError, ValueError, TimeoutError, httpx.HTTPError):
                        pass  # Persist bounded failure; never log callback credentials.
                    self.journal.delivery(task, identity, config, fingerprint, success)
            except Failure:
                pass  # Own-node outages retry reads only; never redispatch execution.
            await anyio.sleep(2)


def create_app(
    gateway: Gateway,
    url: str,
    token: str,
    *,
    journal_path: Path | None = None,
    push_hosts: tuple[str, ...] = (),
    grpc_url: str = "",
    callback_keys: Keyring | None = None,
    policy: Policy | None = None,
    oauth: OAuth | None = None,
) -> ASGIApp:
    from checkedflow.agents.a2a_server import application

    return application(
        gateway,
        url,
        token,
        journal_path=journal_path,
        push_hosts=push_hosts,
        grpc_url=grpc_url,
        callback_keys=callback_keys,
        policy=policy,
        oauth=oauth,
    )


def serve(
    gateway: Gateway,
    host: str,
    port: int,
    token: str,
    *,
    journal_path: Path,
    push_hosts: tuple[str, ...] = (),
    grpc_port: int = 0,
    callback_keys: Keyring | None = None,
    policy: Policy | None = None,
    oauth: OAuth | None = None,
) -> None:
    from checkedflow.agents.a2a_server import serve as run

    run(
        gateway,
        host,
        port,
        token,
        journal_path=journal_path,
        push_hosts=push_hosts,
        grpc_port=grpc_port,
        callback_keys=callback_keys,
        policy=policy,
        oauth=oauth,
    )
