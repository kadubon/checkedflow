"""Official SDK interoperability and adversarial transport/authority regression cases."""

import asyncio
import copy
import json
import socket
import time
from contextlib import asynccontextmanager, contextmanager

import grpc
import httpx
import jwt
import pytest
import uvicorn
from a2a.client import ClientConfig, ClientFactory
from a2a.server.context import ServerCallContext
from a2a.types import a2a_pb2 as pb
from a2a.types import a2a_pb2_grpc as pb_grpc
from a2a.utils.errors import InvalidParamsError, TaskNotCancelableError, TaskNotFoundError
from agent_backend import Backend
from cryptography.hazmat.primitives.asymmetric import rsa
from google.protobuf.json_format import ParseDict
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PromptReference, ResourceTemplateReference
from test_agents import TOKEN, create_command

from checkedflow.agents.a2a import Handler, card, create_app
from checkedflow.agents.gateway import Gateway
from checkedflow.agents.http import validate_json
from checkedflow.agents.journal import Journal
from checkedflow.agents.mcp import create_http_app, create_server
from checkedflow.agents.oauth import OAuth
from checkedflow.agents.push import Push
from checkedflow.core.values import Failure
from checkedflow.wire import dumps


@asynccontextmanager
async def live(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    class TestServer(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            # Several servers share this test process. SSE's process-wide signal watcher
            # must not mistake one fixture's shutdown for another server's shutdown.
            yield

    server = TestServer(
        uvicorn.Config(app, log_level="error", access_log=False, timeout_graceful_shutdown=2)
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(500):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


def request(envelope, *, immediately=True):
    return ParseDict(
        {
            "message": {
                "messageId": envelope["command"]["id"],
                "role": "ROLE_USER",
                "contextId": "m",
                "parts": [
                    {"data": {"operation": "task", "envelopeJson": dumps(envelope).decode()}}
                ],
            },
            "configuration": {"returnImmediately": immediately},
        },
        pb.SendMessageRequest(),
    )


def test_a2a_durable_pagination_filters_history_artifacts_and_cursor_binding(h, tmp_path):
    async def scenario():
        gateway = Backend(h).gateway()
        journal = Journal(gateway, tmp_path / "observations.sqlite", clock=lambda: 100)
        handler, context = Handler(gateway, journal=journal), ServerCallContext()
        h.task("a")
        await handler.refresh()
        h.task("b", start=False)
        first = await handler.on_list_tasks(pb.ListTasksRequest(page_size=1), context)
        assert first.tasks[0].id == "b" and first.total_size == 2 and first.next_page_token
        stamp = first.tasks[0].status.timestamp.ToNanoseconds()
        assert stamp == 101
        journal.close()
        # Clock rollback and restart preserve sorting and authenticated cursors.
        journal = Journal(gateway, tmp_path / "observations.sqlite", clock=lambda: 1)
        handler = Handler(gateway, journal=journal)
        second = await handler.on_list_tasks(
            pb.ListTasksRequest(page_size=1, page_token=first.next_page_token), context
        )
        assert [t.id for t in second.tasks] == ["a"] and not second.next_page_token
        assert not second.tasks[0].artifacts
        task = await handler.on_get_task(pb.GetTaskRequest(id="a", history_length=1000), context)
        assert task.artifacts and len(task.history) == 1
        filters = pb.ListTasksRequest(status=pb.TASK_STATE_SUBMITTED)
        filters.status_timestamp_after.FromNanoseconds(stamp)
        assert [t.id for t in (await handler.on_list_tasks(filters, context)).tasks] == ["b"]
        for token in [first.next_page_token[:-2] + "xx", "not base64"]:
            with pytest.raises(InvalidParamsError):
                await handler.on_list_tasks(
                    pb.ListTasksRequest(page_size=1, page_token=token), context
                )
        with pytest.raises(InvalidParamsError):
            await handler.on_list_tasks(
                pb.ListTasksRequest(page_size=2, page_token=first.next_page_token), context
            )
        h.task("c", start=False)
        with pytest.raises(InvalidParamsError):
            await handler.on_list_tasks(
                pb.ListTasksRequest(page_size=1, page_token=first.next_page_token), context
            )
        assert journal.get("c")[1] > stamp
        journal.close()
        with pytest.raises(Failure, match="another mission"):
            Journal(
                Gateway(gateway.backend, gateway.chain, "other"), tmp_path / "observations.sqlite"
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("binding", ["JSONRPC", "HTTP+JSON"])
def test_a2a_official_bindings_and_streaming(h, binding):
    async def scenario():
        gateway = Backend(h).gateway()
        # The SDK uses advertised endpoints; bind the application at a discovered loopback port.
        app = create_app(gateway, "http://127.0.0.1/rpc", TOKEN)
        async with (
            live(app) as url,
            httpx.AsyncClient(
                headers={"Authorization": "Bearer " + TOKEN},
                timeout=10,
            ) as transport,
        ):
            agent_card = card(url + "/rpc")
            client = ClientFactory(
                ClientConfig(
                    httpx_client=transport, streaming=True, supported_protocol_bindings=[binding]
                )
            ).create(agent_card)
            sent = request(create_command(h))
            stream = client.send_message(sent)
            first = await anext(stream)
            assert (
                first.task.id == "agent-task" and first.task.status.state == pb.TASK_STATE_SUBMITTED
            )
            h.apply("task.lease", {"id": "agent-task"})
            h.apply("task.start", {"id": "agent-task", "fence": 1})
            h.apply(
                "task.finish",
                {
                    "id": "agent-task",
                    "fence": 1,
                    "result": {"outcome": "fail", "evidence": {"reason": "test"}},
                },
            )
            events = [event async for event in stream]
            assert any(e.HasField("artifact_update") for e in events)
            assert events[-1].status_update.status.state == pb.TASK_STATE_FAILED
            listed = await client.list_tasks(pb.ListTasksRequest(include_artifacts=True))
            assert listed.total_size == 1 and listed.tasks[0].artifacts
            extended = await client.get_extended_agent_card(pb.GetExtendedAgentCardRequest())
            assert "Authenticated mission: m" in extended.description
            await client.close()

    asyncio.run(scenario())


def test_a2a_grpc_authentication_and_unary_streaming(h):
    async def scenario():
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        app = create_app(
            Backend(h).gateway(), "http://127.0.0.1/rpc", TOKEN, grpc_url=f"127.0.0.1:{port}"
        )
        async with live(app), grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            client = pb_grpc.A2AServiceStub(channel)
            with pytest.raises(grpc.aio.AioRpcError) as caught:
                await client.ListTasks(pb.ListTasksRequest())
            assert caught.value.code() == grpc.StatusCode.UNAUTHENTICATED
            metadata = (("authorization", "Bearer " + TOKEN), ("a2a-version", "1.0"))
            sent = request(create_command(h))
            response = await client.SendMessage(sent, metadata=metadata)
            assert response.task.id == "agent-task"
            stream = client.SubscribeToTask(
                pb.SubscribeToTaskRequest(id="agent-task"), metadata=metadata
            )
            assert (await stream.read()).task.id == "agent-task"
            stream.cancel()
            assert (
                await client.ListTasks(pb.ListTasksRequest(), metadata=metadata)
            ).total_size == 1

    asyncio.run(scenario())


def test_a2a_subscriptions_blocking_and_signed_cancellation(h):
    async def scenario():
        backend = Backend(h)
        handler, context = Handler(backend.gateway()), ServerCallContext()
        sent = request(create_command(h), immediately=False)
        response = asyncio.create_task(handler.on_message_send(sent, context))
        while "agent-task" not in h.runtime.state.tasks:
            await asyncio.sleep(0.01)
        assert not response.done()
        with pytest.raises(TaskNotCancelableError):
            await handler.on_cancel_task(pb.CancelTaskRequest(id="agent-task"), context)
        h.apply("task.lease", {"id": "agent-task"})
        h.apply("task.start", {"id": "agent-task", "fence": 1})
        h.runtime.tick(h.runtime.state.tasks["agent-task"].deadline + 1)
        assert (await asyncio.wait_for(response, 3)).status.state == pb.TASK_STATE_INPUT_REQUIRED
        signed = h.envelope(
            "task.reconcile",
            {"id": "agent-task", "retry": False, "reason": "effect investigated"},
            admin=True,
        )
        bad = copy.deepcopy(signed)
        bad["signatures"].pop()
        for candidate, accepted in [(bad, False), (signed, True)]:
            params = ParseDict(
                {"id": "agent-task", "metadata": {"envelopeJson": dumps(candidate).decode()}},
                pb.CancelTaskRequest(),
            )
            if accepted:
                assert (
                    await handler.on_cancel_task(params, context)
                ).status.state == pb.TASK_STATE_CANCELED
            else:
                with pytest.raises(InvalidParamsError):
                    await handler.on_cancel_task(params, context)
        with pytest.raises(InvalidParamsError):
            await anext(
                handler.on_subscribe_to_task(pb.SubscribeToTaskRequest(id="agent-task"), context)
            )
        assert backend.calls == 2
        handler.journal.close()

    asyncio.run(scenario())


def test_a2a_push_crud_restart_scoping_and_delivery(h, tmp_path):
    async def scenario():
        h.task(start=False)
        gateway, context = Backend(h).gateway(), ServerCallContext()
        path = tmp_path / "push.sqlite"
        handler = Handler(
            gateway, journal=Journal(gateway, path), push=Push(("callbacks.example",))
        )
        for identity in ["a", "b"]:
            config = await handler.on_create_task_push_notification_config(
                pb.TaskPushNotificationConfig(
                    task_id="t",
                    id=identity,
                    url="https://callbacks.example/event",
                    token="test-only",
                ),
                context,
            )
            assert config.id == identity
        listing = await handler.on_list_task_push_notification_configs(
            pb.ListTaskPushNotificationConfigsRequest(task_id="t", page_size=1), context
        )
        handler.journal.close()
        handler = Handler(
            gateway, journal=Journal(gateway, path), push=Push(("callbacks.example",))
        )
        tail = await handler.on_list_task_push_notification_configs(
            pb.ListTaskPushNotificationConfigsRequest(
                task_id="t", page_size=1, page_token=listing.next_page_token
            ),
            context,
        )
        assert tail.configs[0].id == "b"
        assert (
            await handler.on_get_task_push_notification_config(
                pb.GetTaskPushNotificationConfigRequest(task_id="t", id="a"), context
            )
        ).token == "test-only"
        with pytest.raises(InvalidParamsError):
            await handler.on_get_task_push_notification_config(
                pb.GetTaskPushNotificationConfigRequest(task_id="t", id="a", tenant="other"),
                context,
            )
        await handler.on_delete_task_push_notification_config(
            pb.DeleteTaskPushNotificationConfigRequest(task_id="t", id="a"), context
        )
        with pytest.raises(TaskNotFoundError):
            await handler.on_get_task_push_notification_config(
                pb.GetTaskPushNotificationConfigRequest(task_id="t", id="a"), context
            )
        deliveries = []

        async def deliver(config, task):
            deliveries.append(task)
            return True

        handler.push.deliver = deliver
        monitor = asyncio.create_task(handler.monitor())
        for _ in range(100):
            if deliveries:
                break
            await asyncio.sleep(0.01)
        monitor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor
        assert deliveries[0]["id"] == "t" and not handler.journal.pending()
        handler.journal.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url",
    [
        "http://callbacks.example/event",
        "https://evil.example/event",
        "https://user:password@callbacks.example/",
        "https://callbacks.example:444/",
        "https://callbacks.example/#fragment",
    ],
)
def test_push_origin_policy(url):
    with pytest.raises(Failure):
        Push(("callbacks.example",)).validate({"url": url})


@pytest.mark.parametrize(
    "addresses",
    [["127.0.0.1"], ["::1"], ["169.254.169.254"], ["10.0.0.1"], ["93.184.216.34", "192.168.1.1"]],
)
def test_push_rejects_private_and_mixed_dns(monkeypatch, addresses):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses
        ],
    )
    with pytest.raises(Failure) as caught:
        asyncio.run(Push(("callbacks.example",)).deliver({"url": "https://callbacks.example/"}, {}))
    assert caught.value.code == "PUSH_ADDRESS"


def test_push_dns_pinning_tls_identity_headers_and_no_redirect(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)),
    )
    config = {
        "url": "https://callbacks.example/event",
        "token": "test-only",
        "authentication": {"schemes": ["Bearer"], "credentials": "callback-only"},
    }
    assert not asyncio.run(Push(("callbacks.example",)).deliver(config, {"id": "t"}))
    assert len(calls) == 1 and calls[0].url.host == "93.184.216.34"
    assert calls[0].headers["host"] == "callbacks.example"
    assert calls[0].extensions["sni_hostname"] == b"callbacks.example"
    assert calls[0].headers["authorization"] == "Bearer callback-only"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":"\\ud800"}',
        b'{"\\ud800":1}',
        b"[" * 70 + b"0" + b"]" * 70,
    ],
)
def test_outer_json_security(raw):
    with pytest.raises((ValueError, UnicodeError)):
        validate_json(raw)


@pytest.mark.parametrize("path", ["/rpc", "/v1/tasks", "/v1/extendedAgentCard", "/m/v1/tasks"])
def test_all_a2a_routes_require_auth_and_reject_browser_origin(h, path):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                create_app(Backend(h).gateway(), "http://127.0.0.1/rpc", TOKEN)
            ),
            base_url="http://127.0.0.1",
        ) as client:
            assert (await client.get(path)).status_code == 401
            assert (
                await client.get(
                    path,
                    headers={"Authorization": "Bearer " + TOKEN, "Origin": "https://evil.example"},
                )
            ).status_code == 403

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_mcp_templates_prompts_completion_and_http(h, mode):
    async def scenario():
        h.task()
        async with (
            live(create_http_app(Backend(h).gateway(), TOKEN)) as url,
            httpx.AsyncClient(
                headers={"Authorization": "Bearer " + TOKEN},
                timeout=10,
            ) as http,
            Client(streamable_http_client(url + "/mcp", http_client=http), mode=mode) as client,
        ):
            templates = await client.list_resource_templates()
            assert len(templates.resource_templates) == 3
            assert (
                json.loads((await client.read_resource("checkedflow://tasks/t")).contents[0].text)[
                    "record"
                ]["status"]
                == "finished"
            )
            assert (await client.list_prompts()).prompts[0].name == "checkedflow_review"
            prompt = await client.get_prompt("checkedflow_review", {"identity": "t"})
            assert "untrusted" in prompt.messages[0].content.text
            completion = await client.complete(
                PromptReference(type="ref/prompt", name="checkedflow_review"),
                {"name": "identity", "value": "t"},
            )
            assert completion.completion.values == ["t"]
            completion = await client.complete(
                ResourceTemplateReference(
                    type="ref/resource", uri="checkedflow://tasks/{identity}"
                ),
                {"name": "identity", "value": "t"},
            )
            assert completion.completion.values == ["t"]
            assert not (await client.call_tool("checkedflow_inspect", {})).is_error

    asyncio.run(scenario())


def test_mcp_legacy_sse_and_modern_notifications(h):
    async def scenario():
        gateway = Backend(h).gateway()
        async with (
            live(create_http_app(gateway, TOKEN, transport="sse")) as url,
            Client(
                sse_client(url + "/sse", headers={"Authorization": "Bearer " + TOKEN}),
                mode="legacy",
            ) as client,
        ):
            assert len((await client.list_tools()).tools) == 2
        async with (
            Client(create_server(gateway)) as client,
            client.listen(resource_subscriptions=["checkedflow://mission"]) as changes,
        ):
            await asyncio.sleep(0.1)
            h.task(start=False)
            event = await asyncio.wait_for(anext(aiter(changes)), 3)
            assert event.uri == "checkedflow://mission"

    asyncio.run(scenario())


@pytest.fixture
def oauth_keys(tmp_path):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update(kid="test-public-key", alg="RS256", use="sig")
    path = tmp_path / "public-jwks.json"
    path.write_text(json.dumps({"keys": [public]}))
    return private, path


def oauth_token(private, **changes):
    payload = {
        "iss": "https://issuer.example/",
        "aud": "http://127.0.0.1/mcp",
        "exp": int(time.time()) + 120,
        "sub": "operator",
        "client_id": "test-client",
        "scope": "checkedflow",
    } | changes
    return jwt.encode(payload, private, algorithm="RS256", headers={"kid": "test-public-key"})


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "wrong"},
        {"iss": "https://wrong.example/"},
        {"exp": 1},
        {"sub": None},
        {"client_id": ""},
        {"scope": []},
    ],
)
def test_oauth_rejects_invalid_claims(oauth_keys, changes):
    private, path = oauth_keys
    verifier = OAuth("https://issuer.example/", "http://127.0.0.1/mcp", path)
    assert verifier.verify(oauth_token(private, **changes)) is None


def test_mcp_oauth_metadata_and_scope_enforcement(h, oauth_keys):
    private, path = oauth_keys

    async def scenario():
        verifier = OAuth("https://issuer.example/", "http://127.0.0.1/mcp", path)
        app = create_http_app(Backend(h).gateway(), "", oauth=verifier)
        async with live(app) as url, httpx.AsyncClient(timeout=10) as http:
            assert (await http.post(url + "/mcp", json={})).status_code == 401
            metadata = await http.get(url + "/.well-known/oauth-protected-resource/mcp")
            assert metadata.status_code == 200 and metadata.json()["resource"] == verifier.audience
            http.headers["Authorization"] = "Bearer " + oauth_token(private, scope="unrelated")
            assert (await http.post(url + "/mcp", json={})).status_code == 403
            http.headers["Authorization"] = "Bearer " + oauth_token(private)
            async with Client(streamable_http_client(url + "/mcp", http_client=http)) as client:
                assert len((await client.list_tools()).tools) == 2

    asyncio.run(scenario())


def test_oauth_signature_key_rotation_and_no_token_key_fetch(oauth_keys):
    private, path = oauth_keys
    verifier = OAuth("https://issuer.example/", "http://127.0.0.1/mcp", path)
    token = oauth_token(private)
    assert verifier.verify(token).client_id == "test-client"
    another = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert verifier.verify(oauth_token(another)) is None
    replacement = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(another.public_key()))
    replacement.update(kid="test-public-key", alg="RS256")
    path.write_text(json.dumps({"keys": [replacement]}))
    assert verifier.verify(token) is None
    assert verifier.verify(oauth_token(another)) is not None
    payload = jwt.decode(token, options={"verify_signature": False})
    redirected = jwt.encode(
        payload,
        private,
        algorithm="RS256",
        headers={
            "kid": "uninstalled",
            "jku": "http://127.0.0.1/keys",
            "x5u": "http://169.254.169.254/",
        },
    )
    assert verifier.verify(redirected) is None
    path.write_text(json.dumps({"keys": [replacement, replacement]}))
    assert verifier.verify(oauth_token(another)) is None


def test_push_inflight_replacement_cannot_acknowledge_new_configuration(h):
    h.task(start=False)
    journal = Journal(Backend(h).gateway())
    journal.refresh()
    first = {"url": "https://first.example/", "id": "same"}
    second = {"url": "https://second.example/", "id": "same"}
    journal.put_config("t", "same", first)
    fingerprint = journal.pending()[0][3]
    journal.put_config("t", "same", second)
    journal.delivery("t", "same", first, fingerprint, True)
    assert journal.pending()[0][2] == second
    journal.delivery("t", "same", second, fingerprint, True)
    assert not journal.pending()
    journal.close()


@pytest.mark.parametrize(
    "inner",
    ["{}", '{"command":{}}', '{"command":null}', '{"command":{"kind":"task.create","payload":{}}}'],
)
def test_a2a_malformed_envelopes_are_rejected_before_dispatch(h, inner):
    async def scenario():
        backend = Backend(h)
        handler = Handler(backend.gateway())
        params = ParseDict(
            {
                "message": {
                    "messageId": "invalid",
                    "role": "ROLE_USER",
                    "parts": [{"data": {"operation": "task", "envelopeJson": inner}}],
                }
            },
            pb.SendMessageRequest(),
        )
        with pytest.raises(InvalidParamsError):
            await handler.on_message_send(params, ServerCallContext())
        assert backend.calls == 0
        handler.journal.close()

    asyncio.run(scenario())
