"""Real protocol handlers and transport admission enforce operator-owned client grants."""

import asyncio
import json
import time
from dataclasses import replace

import grpc
import httpx
import pytest
from a2a.server.context import ServerCallContext
from a2a.types import a2a_pb2 as pb
from a2a.types import a2a_pb2_grpc as pb_grpc
from a2a.utils.errors import InvalidParamsError, TaskNotFoundError
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from test_agent_protocols import live, oauth_token
from test_agent_protocols import oauth_keys as key_fixture
from test_agents import TOKEN
from test_operational_gateway import Node

from checkedflow.agents.a2a import Handler, create_app
from checkedflow.agents.access import LOCAL, ROLES, Policy, Principal
from checkedflow.agents.authentication import Authentication, authenticated_headers
from checkedflow.agents.http import Guard
from checkedflow.agents.mcp import create_http_app, create_server
from checkedflow.agents.oauth import OAuth
from checkedflow.agents.operational_gateway import Gateway
from checkedflow.agents.push import Push
from checkedflow.core.values import Failure
from checkedflow.operational_identity import sign_command


@pytest.fixture
def client_keys(tmp_path):
    return key_fixture.__wrapped__(tmp_path)


def grant(principal=LOCAL, **changes):
    return {
        "issuer": principal.issuer,
        "client": principal.client,
        "subject": principal.subject,
        "chain": "operational-test",
        "mission": "m",
        "roles": list(ROLES),
        "actors": ["worker", "a"],
    } | changes


def save(path, grants):
    path.write_text(json.dumps({"profile": "checkedflow/access-policy/v1", "grants": grants}))


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / "access.json"
    save(path, [grant()])
    return Policy(path, "operational-test", "m")


def context(principal=LOCAL):
    return ServerCallContext(state={"checkedflow.principal": principal})


def test_identity_roles_actor_scope_and_live_revocation(policy):
    n = Node()
    command = n.gateway().command(n.envelope())
    before = policy.check(LOCAL)
    policy.command(LOCAL, command)
    for principal in (
        None,
        replace(LOCAL, subject="other"),
        replace(LOCAL, client="other"),
        replace(LOCAL, issuer="other"),
        replace(LOCAL, expires=int(time.time()) - 1),
    ):
        with pytest.raises(Failure, match="ACCESS"):
            policy.check(principal)
    for changed in (
        command | {"chain": "foreign"},
        command | {"actor": "other"},
        command | {"payload": {"mission": "other"}},
        command | {"kind": "unknown"},
        command | {"api_version": "checkedflow/v1"},
    ):
        with pytest.raises(Failure, match="ACCESS"):
            policy.command(LOCAL, changed)
    save(policy.path, [grant(roles=["inspect"])])
    assert policy.check(LOCAL) != before
    with pytest.raises(Failure, match="ACCESS"):
        policy.command(LOCAL, command)
    save(policy.path, [])
    with pytest.raises(Failure, match="ACCESS"):
        policy.check(LOCAL)
    policy.path.unlink()
    with pytest.raises(Failure, match="ACCESS"):
        policy.check(LOCAL)


@pytest.mark.parametrize(
    "changes",
    [
        {"roles": ["invented"]},
        {"roles": ["inspect", "inspect"]},
        {"actors": ["a", "a"]},
        {"roles": "inspect"},
        {"actors": [""]},
        {"extra": True},
        {"client": ""},
    ],
)
def test_invalid_grants_fail_closed(policy, changes):
    save(policy.path, [grant(**changes)])
    with pytest.raises(Failure):
        policy.check(LOCAL)


def test_policy_file_bounds_duplicates_and_version(policy):
    save(policy.path, [grant(), grant()])
    with pytest.raises(Failure):
        policy.check(LOCAL)
    for raw in (b'{"profile":"wrong","grants":[]}', b'{"grants":[],"grants":[]}', b"a" * 65537):
        policy.path.write_bytes(raw)
        with pytest.raises(Failure):
            policy.check(LOCAL)


def test_authentication_never_accepts_unverified_principals(client_keys):
    private, path = client_keys
    oauth = OAuth("https://issuer.example/", "http://127.0.0.1/mcp", path)
    auth = Authentication("", oauth)
    principal = authenticated_headers(["Bearer " + oauth_token(private)], auth.verify)
    assert principal.issuer == oauth.issuer and principal.subject == "operator"
    assert Principal.restore(principal.record()) == principal
    assert oauth.verify(oauth_token(private)).claims == {"iss": oauth.issuer}
    for headers in (
        [],
        ["Bearer x", "Bearer y"],
        ["Basic x"],
        ["Bearer " + oauth_token(private, scope="other")],
    ):
        with pytest.raises(Failure):
            authenticated_headers(headers, auth.verify)
    assert authenticated_headers(["Bearer " + TOKEN], Authentication(TOKEN).verify) == LOCAL
    with pytest.raises(Failure):
        authenticated_headers(["Bearer incorrect"], Authentication(TOKEN).verify)
    with pytest.raises(Failure):
        Authentication("short")


def test_mcp_common_policy_checks_tools_resources_prompts_and_actor(policy):
    async def scenario():
        n = Node()
        server = create_server(n.gateway(), policy=policy)
        async with Client(server) as client:
            assert (await client.call_tool("checkedflow_inspect", {})).structured_content[
                "mission"
            ] == "m"
            save(policy.path, [grant(roles=["inspect"])])
            with pytest.raises(MCPError, match="Client access denied"):
                await client.call_tool("checkedflow_submit", {"envelope_json": n.envelope()})
            assert not n.sent
            save(policy.path, [grant(actors=["other"])])
            with pytest.raises(MCPError, match="Client access denied"):
                await client.call_tool("checkedflow_submit", {"envelope_json": n.envelope()})
            save(policy.path, [grant()])
            result = await client.call_tool("checkedflow_submit", {"envelope_json": n.envelope()})
            assert result.structured_content["status"] == "committed" and len(n.sent) == 1
            save(policy.path, [])
            with pytest.raises(MCPError, match="Client access denied"):
                await client.read_resource("checkedflow://mission")
            with pytest.raises(MCPError, match="Client access denied"):
                await client.get_prompt("checkedflow_review", {"identity": n.task})

    asyncio.run(scenario())


def test_a2a_callback_ownership_and_client_bound_pagination(policy):
    async def scenario():
        n = Node()
        second = replace(LOCAL, client="second")
        save(policy.path, [grant(), grant(second)])
        handler = Handler(n.gateway(), policy=policy, push=Push(("callbacks.example",)))
        try:
            with pytest.raises(InvalidParamsError, match="ACCESS"):
                await handler.on_get_task(pb.GetTaskRequest(id=n.task), ServerCallContext())
            configs = [
                pb.TaskPushNotificationConfig(
                    id=i, task_id=n.task, url="https://callbacks.example/", token="fixture-secret"
                )
                for i in ("a", "b")
            ]
            for config in configs:
                created = await handler.on_create_task_push_notification_config(config, context())
                assert not created.token
            request = pb.GetTaskPushNotificationConfigRequest(task_id=n.task, id="a")
            with pytest.raises(TaskNotFoundError):
                await handler.on_get_task_push_notification_config(request, context(second))
            with pytest.raises(TaskNotFoundError):
                await handler.on_create_task_push_notification_config(configs[0], context(second))
            with pytest.raises(TaskNotFoundError):
                await handler.on_delete_task_push_notification_config(
                    pb.DeleteTaskPushNotificationConfigRequest(task_id=n.task, id="a"),
                    context(second),
                )
            listed = await handler.on_list_task_push_notification_configs(
                pb.ListTaskPushNotificationConfigsRequest(task_id=n.task, page_size=1), context()
            )
            assert listed.next_page_token and len(listed.configs) == 1
            hidden = await handler.on_list_task_push_notification_configs(
                pb.ListTaskPushNotificationConfigsRequest(task_id=n.task), context(second)
            )
            assert not hidden.configs
            with pytest.raises(InvalidParamsError, match="CURSOR"):
                await handler.on_list_task_push_notification_configs(
                    pb.ListTaskPushNotificationConfigsRequest(
                        task_id=n.task, page_size=1, page_token=listed.next_page_token
                    ),
                    context(second),
                )
            tasks = await handler.on_list_tasks(pb.ListTasksRequest(), context())
            assert len(tasks.tasks) == 1
            save(policy.path, [])
            with pytest.raises(InvalidParamsError, match="ACCESS"):
                await handler.on_get_task(pb.GetTaskRequest(id=n.task), context())
        finally:
            handler.journal.close()

    asyncio.run(scenario())


def test_a2a_stream_stops_after_client_revocation(policy):
    async def scenario():
        n = Node()
        handler = Handler(n.gateway(), policy=policy)
        try:
            stream = handler.on_subscribe_to_task(pb.SubscribeToTaskRequest(id=n.task), context())
            assert (await anext(stream)).id == n.task
            save(policy.path, [])
            with pytest.raises(InvalidParamsError, match="ACCESS"):
                await anext(stream)
        finally:
            handler.journal.close()

    asyncio.run(scenario())


def test_administration_still_requires_committed_quorum_and_access_role(policy):
    async def scenario():
        n = Node()
        gateway = Gateway(n, "operational-test", "m", administration=True)
        command = n.h.template | {
            "id": "0:pause-client",
            "epoch": n.read().journal.epoch,
            "kind": "mission.pause",
            "actor": "a",
            "nonce": dict(n.read().journal.actors)["a"] + 1,
            "payload": {"mission": "m"},
        }
        raw = sign_command(
            command, {pair: key for pair, key in n.h.keys.items() if pair[0] in "abc"}
        ).decode()
        server = create_server(gateway, policy=policy)
        async with Client(server) as client:
            save(policy.path, [grant(roles=["inspect", "approve"])])
            with pytest.raises(MCPError, match="Client access denied"):
                await client.call_tool("checkedflow_submit", {"envelope_json": raw})
            assert not n.sent
            save(policy.path, [grant(roles=["inspect", "operate"])])
            bad = sign_command(command, {("a", 1): n.h.keys[("a", 1)]}).decode()
            rejected = await client.call_tool("checkedflow_submit", {"envelope_json": bad})
            assert rejected.is_error and not n.sent
            result = await client.call_tool("checkedflow_submit", {"envelope_json": raw})
            assert result.structured_content["status"] == "committed"
            assert n.read().mode == "paused"

    asyncio.run(scenario())


def test_http_and_grpc_use_verified_identity(policy, client_keys):
    async def scenario():
        import socket

        private, keys = client_keys
        oauth = OAuth("https://issuer.example/", "http://127.0.0.1/mcp", keys)
        principal = Principal(oauth.issuer, "test-client", "operator")
        save(policy.path, [grant(principal)])
        n = Node()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        app = create_app(
            n.gateway(),
            "http://127.0.0.1/rpc",
            "",
            oauth=oauth,
            policy=policy,
            grpc_url=f"127.0.0.1:{port}",
        )
        async with live(app) as url, httpx.AsyncClient(base_url=url) as http:
            http.headers.update(
                {"Authorization": "Bearer " + oauth_token(private), "A2A-Version": "1.0"}
            )
            assert (await http.get("/v1/tasks")).status_code == 200
            http.headers["Authorization"] = "Bearer " + oauth_token(private, sub="other")
            assert (await http.get("/v1/tasks")).status_code == 403
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = pb_grpc.A2AServiceStub(channel)
                metadata = (
                    ("authorization", "Bearer " + oauth_token(private)),
                    ("a2a-version", "1.0"),
                )
                assert (
                    await stub.GetTask(pb.GetTaskRequest(id=n.task), metadata=metadata)
                ).id == n.task
                save(policy.path, [])
                with pytest.raises(grpc.aio.AioRpcError) as caught:
                    await stub.GetTask(pb.GetTaskRequest(id=n.task), metadata=metadata)
                assert caught.value.code() == grpc.StatusCode.PERMISSION_DENIED
        save(policy.path, [grant(principal)])
        mcp_app = create_http_app(n.gateway(), "", oauth=oauth, policy=policy)
        async with live(mcp_app) as url, httpx.AsyncClient(base_url=url) as http:
            assert (await http.get("/.well-known/oauth-protected-resource/mcp")).status_code == 200
            challenge = await http.post("/mcp", json={})
            assert (
                challenge.status_code == 401
                and "resource_metadata=" in challenge.headers["www-authenticate"]
            )
            http.headers["Authorization"] = "Bearer " + oauth_token(private)
            async with Client(streamable_http_client(url + "/mcp", http_client=http)) as client:
                inspected = await client.call_tool("checkedflow_inspect", {})
                assert inspected.structured_content["mission"] == "m"
                save(policy.path, [grant(principal, roles=["inspect"])])
                with pytest.raises(MCPError, match="Client access denied"):
                    await client.call_tool("checkedflow_submit", {"envelope_json": n.envelope()})
                assert not n.sent
            headers = {"Authorization": "Bearer " + oauth_token(private, client_id="other")}
            assert (await http.post("/mcp", headers=headers, json={})).status_code == 403

    asyncio.run(scenario())


def test_http_rechecks_policy_before_each_stream_chunk(policy):
    async def scenario():
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"permitted", "more_body": True})
            save(policy.path, [])
            await send({"type": "http.response.body", "body": b"must-not-leak", "more_body": False})

        guarded = Guard(
            app, None, public=(), authenticate=Authentication(TOKEN).verify, policy=policy
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=guarded), base_url="http://test"
        ) as http:
            response = await http.get("/events", headers={"Authorization": "Bearer " + TOKEN})
            assert response.content == b"permitted"

    asyncio.run(scenario())


def test_sdk_oauth_context_requires_verified_issuer_and_scope():
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    from checkedflow.agents.authentication import oauth_principal

    assert oauth_principal() is None
    token = AccessToken(
        token="fixture",
        client_id="client",
        scopes=["checkedflow"],
        subject="subject",
        expires_at=int(time.time()) + 60,
    )
    handle = auth_context_var.set(AuthenticatedUser(token))
    try:
        assert oauth_principal() is None
        token.claims = {"iss": "https://issuer.example/"}
        assert oauth_principal().client == "client"
        token.scopes = []
        assert oauth_principal() is None
    finally:
        auth_context_var.reset(handle)


def test_a2a_command_role_does_not_come_from_message_metadata(policy):
    from google.protobuf.json_format import ParseDict

    async def scenario():
        n = Node()
        handler = Handler(n.gateway(), policy=policy)
        try:
            params = ParseDict(
                {
                    "message": {
                        "messageId": "0:transport",
                        "role": "ROLE_USER",
                        "parts": [{"data": {"operation": "submit", "envelopeJson": n.envelope()}}],
                        "metadata": {"roles": ["submit"], "principal": "operator"},
                    }
                },
                pb.SendMessageRequest(),
            )
            save(policy.path, [grant(roles=["inspect"])])
            with pytest.raises(InvalidParamsError, match="ACCESS"):
                await handler.on_message_send(params, context())
            assert not n.sent
            save(policy.path, [grant()])
            response = await handler.on_message_send(params, context())
            assert response.message_id == "reply:0:transport" and len(n.sent) == 1
        finally:
            handler.journal.close()

    asyncio.run(scenario())


def test_mcp_subscription_rechecks_owner_before_publishing(policy):
    async def scenario():
        n = Node()
        async with (
            Client(create_server(n.gateway(), policy=policy)) as client,
            client.listen(resource_subscriptions=["checkedflow://mission"]) as changes,
        ):
            await asyncio.sleep(0.1)
            n.gateway().submit(n.envelope())
            assert (await asyncio.wait_for(anext(aiter(changes)), 3)).uri == "checkedflow://mission"
            save(policy.path, [])
            n.h.send("mission.pause", {})
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(anext(aiter(changes)), 0.8)
            save(policy.path, [grant()])

    asyncio.run(scenario())


def test_callback_rechecks_grant_after_dns_before_dispatch(policy, monkeypatch):
    import socket

    def resolve(*args, **kwargs):
        save(policy.path, [])
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(Failure, match="ACCESS"):
        asyncio.run(
            Push(("callbacks.example",)).deliver(
                {"url": "https://callbacks.example/"}, {}, authorize=lambda: policy.check(LOCAL)
            )
        )


def test_callback_monitor_preserves_but_does_not_send_revoked_owner(policy, monkeypatch):
    async def scenario():
        n = Node()
        handler = Handler(n.gateway(), policy=policy, push=Push(("callbacks.example",)))
        attempts = []

        async def denied_dispatch(*args, **kwargs):
            attempts.append(True)
            return True

        monkeypatch.setattr(handler.push, "deliver", denied_dispatch)
        try:
            await handler.on_create_task_push_notification_config(
                pb.TaskPushNotificationConfig(
                    id="owned", task_id=n.task, url="https://callbacks.example/"
                ),
                context(),
            )
            save(policy.path, [])
            monitor = asyncio.create_task(handler.monitor())
            try:
                await asyncio.sleep(0.1)
                assert not attempts
                assert handler.journal.pending()[0][4] == 1
            finally:
                monitor.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await monitor
        finally:
            handler.journal.close()

    asyncio.run(scenario())


def test_mismatched_policy_is_not_a_gateway_alias(policy):
    other = Policy(policy.path, "other-chain", "m")
    with pytest.raises(Failure, match="ACCESS"):
        create_server(Node().gateway(), policy=other)
    with pytest.raises(Failure, match="ACCESS"):
        Handler(Node().gateway(), policy=other)


def test_portable_policy_vectors_and_packaged_schema(tmp_path):
    from importlib.resources import files

    from jsonschema import Draft202012Validator

    data = files("checkedflow").joinpath("data")
    vectors = json.loads(data.joinpath("access-vectors.json").read_text())
    schema = json.loads(data.joinpath("access-policy.schema.json").read_text())
    Draft202012Validator(schema).validate(vectors["policy"])
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(vectors["policy"]))
    policy = Policy(path, vectors["chain"], vectors["mission"])
    for case in vectors["cases"]:
        principal = Principal.restore(case["principal"])
        try:
            policy.check(principal, case["role"], actor=case["actor"])
            outcome = "ALLOW"
        except Failure as exc:
            outcome = exc.code
        assert outcome == case["result"]


def test_v2_cli_cannot_start_without_operator_policy():
    import subprocess
    import sys

    for mode in ("a2a", "mcp"):
        extra = ["--journal", "unused-private-journal"] if mode == "a2a" else []
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "checkedflow.cli",
                mode,
                "--protocol",
                "v2",
                "--chain",
                "c",
                "--mission",
                "m",
                "--rpc",
                "http://127.0.0.1:1",
                *extra,
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
        assert result.returncode != 0
        assert b"v2 requires --access-policy" in result.stderr
