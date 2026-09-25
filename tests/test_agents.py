import asyncio
import copy
import json
import sys
from importlib.resources import files
from pathlib import Path

import httpx
import pytest
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import a2a_pb2 as pb
from agent_backend import Backend
from google.protobuf.json_format import MessageToDict, ParseDict
from jsonschema import Draft202012Validator
from mcp import Client, StdioServerParameters

from checkedflow.agents.a2a import MAX_BODY, create_app, task_projection
from checkedflow.agents.gateway import Gateway, profile
from checkedflow.agents.mcp import create_server
from checkedflow.core.values import Failure
from checkedflow.identity import sign
from checkedflow.wire import dumps

TOKEN = "public-test-only-bearer-value-00000000"


def create_command(h, identity="agent-task", mission="m"):
    return h.envelope(
        "task.create",
        {
            "id": identity,
            "mission": mission,
            "phase": "generate",
            "spec": {"target": ["double"]},
            "dependencies": [],
            "cost": 2,
            "ttl": 1000,
            "effect": "isolated",
        },
    )


def test_gateway_shared_state_idempotency_and_lost_response(h):
    backend = Backend(h)
    first, restarted = backend.gateway(), backend.gateway()
    envelope = dumps(create_command(h)).decode()
    backend.lose_response = True
    with pytest.raises(Failure, match="query committed state") as caught:
        first.submit(envelope)
    assert caught.value.code == "OUTCOME_UNKNOWN" and backend.calls == 1
    assert restarted.inspect("task", "agent-task")["record"]["status"] == "ready"
    backend.lose_response = False
    acknowledgment = restarted.submit(envelope)
    assert acknowledgment["status"] == "committed"
    assert acknowledgment["verification"] == "not_implied"
    assert backend.calls == 1  # A committed duplicate never reenters the node's mempool cache.
    assert len(backend.state().tasks) == 1
    assert backend.state().missions["m"].spent == 0


@pytest.mark.parametrize("defect", ["signature", "nonce", "chain", "id", "global", "scope"])
def test_gateway_rejects_before_dispatch(h, defect):
    backend = Backend(h)
    envelope = create_command(h)
    message_id = ""
    if defect == "signature":
        envelope["signatures"][0]["signature"] = "0" * 128
    elif defect == "global":
        envelope = h.envelope("worker.revoke", {"id": "w0", "reason": "test"}, admin=True)
    elif defect == "id":
        message_id = "mismatch"
    else:
        command = envelope["command"]
        if defect == "nonce":
            command["nonce"] += 1
        elif defect == "chain":
            command["chain"] = "other-chain"
        else:
            command["payload"]["mission"] = "other-mission"
        envelope = sign(command, {"w0": h.keys["w0"]})
    with pytest.raises(Failure):
        backend.gateway().submit(dumps(envelope).decode(), message_id=message_id)
    assert backend.calls == 0 and not backend.state().tasks


@pytest.mark.parametrize(
    "raw",
    [
        '{"command":{},"command":{}}',
        '{"x":1.0}',
        '{"x":9007199254740992}',
        " " * 1048577,
        '{"x":"\ud800"}',
    ],
    ids=["duplicate-keys", "float-token", "unsafe-integer", "oversize", "surrogate"],
)
def test_signed_text_rejects_lexical_ambiguity_and_limits(h, raw):
    backend = Backend(h)
    with pytest.raises(Failure):
        backend.gateway().submit(raw)
    assert backend.calls == 0


def test_gateway_scope_reads_and_revoked_reuse(h):
    h.checked()
    gateway = Backend(h).gateway()
    assert gateway.inspect("capability", "c")["record"]["status"] == "checked"
    h.apply("capability.revoke", {"id": "c", "reason": "withdrawn evidence"}, admin=True)
    assert gateway.inspect("capability", "c")["record"]["status"] == "revoked"
    envelope = create_command(h)
    envelope["command"]["payload"]["dependencies"] = ["c"]
    envelope = sign(envelope["command"], {"w0": h.keys["w0"]})
    with pytest.raises(Failure):
        gateway.submit(dumps(envelope).decode())
    h.apply("mission.create", h.mission(id="other"), admin=True)
    foreign = Gateway(gateway.backend, h.runtime.state.chain, "other")
    for kind, identity in [("task", "t"), ("capability", "c")]:
        with pytest.raises(Failure) as caught:
            foreign.inspect(kind, identity)
        assert caught.value.code == "NOT_FOUND"
    assert foreign.inspect("tasks")["records"] == {}


def test_no_unconfirmed_success(h):
    backend = Backend(h)
    backend.submit = lambda envelope: {"apparently": "ok"}
    with pytest.raises(Failure) as caught:
        backend.gateway().submit(dumps(create_command(h)).decode())
    assert caught.value.code == "OUTCOME_UNKNOWN"


def test_gateway_scoped_governance_still_requires_three_signatures(h):
    h.checked()
    backend = Backend(h)
    envelope = h.envelope("capability.revoke", {"id": "c", "reason": "new evidence"}, admin=True)
    insufficient = copy.deepcopy(envelope)
    insufficient["signatures"].pop()
    with pytest.raises(Failure):
        backend.gateway().submit(dumps(insufficient).decode())
    assert backend.calls == 0 and h.runtime.state.capabilities["c"].status == "checked"
    backend.gateway().submit(dumps(envelope).decode())
    assert h.runtime.state.capabilities["c"].status == "revoked"


def test_gateway_does_not_trust_wrong_chain_or_unavailable_node(h):
    backend = Backend(h)
    with pytest.raises(Failure) as caught:
        Gateway(backend, "wrong-chain", "m").inspect()
    assert caught.value.code == "CHAIN"

    def unavailable():
        raise TimeoutError("unavailable")

    backend.state = unavailable
    with pytest.raises(Failure) as caught:
        backend.gateway().inspect()
    assert caught.value.code == "UNAVAILABLE"


@pytest.mark.parametrize(
    "reply,expected",
    [
        ({}, "OUTCOME_UNKNOWN"),
        ({"check_tx": {"code": 0}}, "OUTCOME_UNKNOWN"),
        ({"tx_result": {"code": 0}}, "OUTCOME_UNKNOWN"),
        ({"check_tx": {"code": 1}}, "REJECTED"),
        ({"check_tx": {"code": 0}, "tx_result": {"code": 1}}, "REJECTED"),
    ],
)
def test_incomplete_node_responses_are_not_definite_rejection(reply, expected):
    from checkedflow.distributed.client import Client as NodeClient

    client = NodeClient("http://127.0.0.1:26657")
    client.rpc = lambda method, params: reply
    with pytest.raises(Failure) as caught:
        client.submit({})
    assert caught.value.code == expected


def test_a2a_official_client_and_mcp_observe_same_commit(h):
    async def scenario():
        gateway = Backend(h).gateway()
        app = create_app(gateway, "http://127.0.0.1/rpc", TOKEN)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer " + TOKEN},
        ) as transport:
            card = await A2ACardResolver(transport, "http://127.0.0.1").get_agent_card()
            assert card.supported_interfaces[0].protocol_version == "1.0"
            client = ClientFactory(ClientConfig(httpx_client=transport, streaming=False)).create(
                card
            )
            envelope = create_command(h)
            request = ParseDict(
                {
                    "message": {
                        "messageId": envelope["command"]["id"],
                        "contextId": "m",
                        "role": "ROLE_USER",
                        "parts": [
                            {
                                "data": {
                                    "operation": "submit",
                                    "envelopeJson": dumps(envelope).decode(),
                                }
                            }
                        ],
                    }
                },
                pb.SendMessageRequest(),
            )
            responses = [event async for event in client.send_message(request)]
            ack = json.loads(MessageToDict(responses[0].message.parts[0].data)["checkedflowJson"])
            assert ack["status"] == "committed"
            task = await client.get_task(pb.GetTaskRequest(id="agent-task"))
            assert task.status.state == pb.TASK_STATE_SUBMITTED
            assert not task.history and not task.artifacts
            async with Client(create_server(gateway)) as mcp:
                result = await mcp.call_tool(
                    "checkedflow_inspect", {"kind": "task", "identity": task.id}
                )
                assert not result.is_error
                assert result.structured_content["state_hash"] == ack["state_hash"]
                # The same exact envelope through another protocol cannot create another work task.
                result = await mcp.call_tool(
                    "checkedflow_submit", {"envelope_json": dumps(envelope).decode()}
                )
                assert not result.is_error and len(h.runtime.state.tasks) == 1

    asyncio.run(scenario())


def test_a2a_authentication_versions_and_cancellation_authority(h):
    async def scenario():
        backend = Backend(h)
        app = create_app(backend.gateway(), "http://127.0.0.1/rpc", TOKEN)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1"
        ) as client:
            assert (await client.get("/.well-known/agent-card.json")).status_code == 200
            assert (await client.post("/rpc", json={})).status_code == 401
            client.headers["Authorization"] = "Bearer " + TOKEN
            client.headers["A2A-Version"] = "1.0"
            duplicate = await client.post("/rpc", content='{"id":1,"id":2}')
            assert duplicate.status_code == 400
            huge = await client.post("/rpc", content=b" " * (MAX_BODY + 1))
            assert huge.status_code == 413

            async def rpc(method, params=None, **kwargs):
                return (
                    await client.post(
                        "/rpc",
                        json={
                            "jsonrpc": "2.0",
                            "id": "1",
                            "method": method,
                            "params": params or {},
                        },
                        **kwargs,
                    )
                ).json()

            assert (await rpc("ListTasks"))["result"]["totalSize"] == 0
            assert (await rpc("GetTask", {"id": "missing"}))["error"]["code"] == -32001
            assert (await rpc("GetTask", {"id": "missing"}, headers={"A2A-Version": "99.0"}))[
                "error"
            ]["code"] == -32009
            h.task(start=False)
            assert (await rpc("CancelTask", {"id": "t"}))["error"]["code"] == -32002
            assert h.runtime.state.tasks["t"].status == "ready"
            assert backend.calls == 0

    asyncio.run(scenario())


def test_a2a_lost_response_preserves_unknown_then_confirms_without_redispatch(h):
    async def scenario():
        backend = Backend(h)
        backend.lose_response = True
        app = create_app(backend.gateway(), "http://127.0.0.1/rpc", TOKEN)
        envelope = create_command(h)
        request = {
            "jsonrpc": "2.0",
            "id": "rpc-1",
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": envelope["command"]["id"],
                    "role": "ROLE_USER",
                    "parts": [
                        {"data": {"operation": "submit", "envelopeJson": dumps(envelope).decode()}},
                    ],
                },
            },
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer " + TOKEN, "A2A-Version": "1.0"},
        ) as client:
            first = (await client.post("/rpc", json=request)).json()
            assert first["error"]["code"] == -32603
            detail = next(
                item
                for item in first["error"]["data"]
                if item["@type"] == "type.googleapis.com/google.rpc.ErrorInfo"
            )
            assert detail["metadata"]["checkedflowCode"] == "OUTCOME_UNKNOWN"
            second = (await client.post("/rpc", json=request)).json()
            assert "message" in second["result"] and backend.calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_mcp_real_stdio_discovery_resources_tools_and_errors(mode):
    async def scenario():
        from conftest import Harness

        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-u", str(Path(__file__).with_name("agent_stdio.py"))],
        )
        async with Client(parameters, mode=mode, read_timeout_seconds=20) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "checkedflow_inspect",
                "checkedflow_submit",
            }
            resources = await client.list_resources()
            assert len(resources.resources) == 3
            resource = await client.read_resource("checkedflow://profile")
            assert json.loads(resource.contents[0].text) == profile()
            harness = Harness()
            envelope = create_command(harness)
            result = await client.call_tool(
                "checkedflow_submit", {"envelope_json": dumps(envelope).decode()}
            )
            assert not result.is_error and result.structured_content["status"] == "committed"
            malformed = copy.deepcopy(envelope)
            malformed["signatures"] = []
            result = await client.call_tool(
                "checkedflow_submit", {"envelope_json": json.dumps(malformed)}
            )
            assert result.is_error and result.structured_content["error"] == "SCHEMA"
            result = await client.call_tool(
                "checkedflow_inspect", {"kind": "task", "identity": "agent-task"}
            )
            assert result.structured_content["record"]["status"] == "ready"

    asyncio.run(scenario())


def test_task_projection_never_promotes_completion_to_verification(h):
    h.task()
    value = Backend(h).gateway().inspect("task", "t")
    task = task_projection("t", value)
    assert task.status.state == pb.TASK_STATE_COMPLETED
    assert json.loads(MessageToDict(task.metadata)["checkedflowJson"])["capabilities"] == {}


def test_portable_agent_profile_vectors():
    resources = files("checkedflow").joinpath("data")
    vectors = json.loads(resources.joinpath("agent-vectors.json").read_text())
    schema = Draft202012Validator(
        json.loads(resources.joinpath("agent-request.schema.json").read_text())
    )
    for case in vectors["requests"]:
        assert schema.is_valid(case["input"]) is case["valid"]
    for case in vectors["projections"]:
        task = task_projection(
            "t",
            {
                "mission": "m",
                "record": {"status": case["status"], "result": {"outcome": case["outcome"]}},
            },
        )
        assert pb.TaskState.Name(task.status.state) == case["a2a"]
