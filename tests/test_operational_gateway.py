"""Native v2 signatures/views through the shared transport implementations."""

import asyncio
import json
from dataclasses import replace

import pytest
from a2a.server.context import ServerCallContext
from a2a.types import a2a_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict
from mcp import Client
from test_worker_submission import Node as WorkerNode

from checkedflow.agents.a2a import Handler, task_projection
from checkedflow.agents.journal import Journal
from checkedflow.agents.mcp import create_server
from checkedflow.agents.operational_gateway import Gateway
from checkedflow.core.values import Failure
from checkedflow.operational_identity import sign_command
from checkedflow.wire import document


class Node(WorkerNode):
    def live_state(self):
        return self.read()

    def envelope(self, kind="task.lease", payload=None):
        state = self.read()
        command = self.h.template | {
            "epoch": state.journal.epoch,
            "id": "0:transport",
            "actor": "worker",
            "kind": kind,
            "nonce": dict(state.journal.actors)["worker"] + 1,
            "payload": payload or self.lease(),
        }
        return sign_command(command, {("worker", 1): self.h.keys[("worker", 1)]}).decode()

    def gateway(self):
        return Gateway(self, self.read().chain, "m")


def test_original_bytes_commit_duplicate_and_native_views():
    node = Node()
    g = node.gateway()
    raw = node.envelope()
    # Preserve noncanonical whitespace in transport; signature canonicalization is separate.
    raw = json.dumps(json.loads(raw), indent=2)
    assert g.submit(raw, message_id="0:transport")["status"] == "committed"
    assert node.sent == [raw.encode()]
    assert g.submit(raw)["verification"] == "not_implied" and len(node.sent) == 1
    assert g.inspect("task", node.task)["record"]["status"] == "leased"
    assert node.task in g.inspect("tasks")["records"]
    assert g.inspect()["accounting"]["spent"] == 0
    assert g.transport_profile()["command_protocol"] == "checkedflow/v2"
    assert "task.create" not in g.transport_profile()["submit_kinds"]
    assert json.loads(g.envelope_schema())["type"] == "object"
    assert g.journal_binding() == node.gateway().journal_binding()


@pytest.mark.parametrize("behavior", ["before", "after", "empty"])
def test_ambiguous_transport_never_retransmits(behavior):
    n = Node()
    n.behavior = behavior
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        n.gateway().submit(n.envelope())
    assert len(n.sent) == 1


@pytest.mark.parametrize(
    "change,code",
    [
        ("mission", "SCOPE"),
        ("chain", "CHAIN"),
        ("profile", "VERSION"),
    ],
)
def test_wrong_backend_rejected(change, code):
    n = Node()
    n.h.runtime._state = replace(n.read(), **{change: "wrong"})
    g = Gateway(n, "operational-test", "m")
    with pytest.raises(Failure, match=code):
        g.snapshot()


def test_bad_inputs_governance_signature_and_retired_duplicate():
    n = Node()
    g = n.gateway()
    with pytest.raises(Failure, match="ID"):
        g.submit(n.envelope(), message_id="different")
    with pytest.raises(Failure, match="SCOPE"):
        g.submit(n.envelope("mission.pause"))
    with pytest.raises(Failure, match="SCOPE"):
        g.submit(n.envelope(payload={"mission": "foreign", "task": n.task}))
    with pytest.raises(Failure, match="ENCODING"):
        g.command("\ud800")
    with pytest.raises(Failure, match="DUPLICATE_KEY"):
        g.command('{"command":{},"command":{}}')
    raw = n.envelope()
    bad = json.loads(raw)
    bad["signatures"][0]["signature"] = "0" * 128
    with pytest.raises(Failure, match="SIGNATURE"):
        g.submit(json.dumps(bad))
    assert not n.sent
    g.submit(raw)
    n.h.send("journal.rollover", {})
    with pytest.raises(Failure, match="RETIRED_REQUEST"):
        g.submit(raw)
    assert len(n.sent) == 1
    for kind in ("residual", "capability", "task"):
        with pytest.raises(Failure, match="NOT_FOUND"):
            g.inspect(kind, "missing")
    with pytest.raises(Failure, match="SHAPE"):
        g.inspect("invalid")
    with pytest.raises(Failure, match="SCOPE"):
        g.cancellation({}, "unknown")


def test_a2a_v2_submission_history_and_unknown_projection(tmp_path):
    async def run():
        n = Node()
        g = n.gateway()
        journal = Journal(g, tmp_path / "journal.sqlite")
        try:
            handler = Handler(g, journal=journal)
            request = ParseDict(
                {
                    "message": {
                        "messageId": "0:transport",
                        "role": "ROLE_USER",
                        "parts": [{"data": {"operation": "task", "envelopeJson": n.envelope()}}],
                    },
                    "configuration": {"returnImmediately": True},
                },
                pb.SendMessageRequest(),
            )
            task = await handler.on_message_send(request, ServerCallContext())
            assert task.id == n.task and task.status.state == pb.TASK_STATE_WORKING
            observed = await handler.on_get_task(pb.GetTaskRequest(id=n.task), ServerCallContext())
            assert observed.id == n.task
            n.h.send("task.start", {"task": n.task, "fence": 1}, "worker")
            n.h.send(
                "task.finish",
                {"task": n.task, "fence": 1, "outcome": "unknown", "evidence": "c" * 64},
                "worker",
            )
            await handler.refresh()
            assert handler.project(n.task).status.state == pb.TASK_STATE_INPUT_REQUIRED
            artifact = handler.project(n.task).artifacts[0]
            assert "not_implied" in MessageToDict(artifact.parts[0])["data"]["receiptJson"]
        finally:
            journal.close()

    asyncio.run(run())


def test_official_mcp_client_uses_same_v2_gateway():
    async def run():
        n = Node()
        async with Client(create_server(n.gateway())) as client:
            before = await client.call_tool("checkedflow_inspect", {"kind": "mission"})
            assert not before.is_error
            result = await client.call_tool("checkedflow_submit", {"envelope_json": n.envelope()})
            assert not result.is_error and len(n.sent) == 1
            profile = await client.read_resource("checkedflow://profile")
            assert "checkedflow/v2" in profile.contents[0].text

    asyncio.run(run())


def test_effect_executor_submission_and_native_mission_projection():
    from test_work_effects import prepared

    async def run():
        n = Node()
        n.h = prepared()
        n.h.action("authorize")
        g = n.gateway()
        state = n.read()
        command = n.h.template | {
            "epoch": state.journal.epoch,
            "id": "0:effect-agent",
            "actor": "effects",
            "revision": 1,
            "nonce": dict(state.journal.actors)["effects"] + 1,
            "kind": "effect.reserve",
            "payload": {"mission": "m", "effect": n.h.effect},
        }
        raw = sign_command(command, {("effects", 1): n.h.keys[("effects", 1)]}).decode()
        async with Client(create_server(g)) as client:
            result = await client.call_tool("checkedflow_submit", {"envelope_json": raw})
            assert not result.is_error and n.sent == [raw.encode()]
            assert g.inspect()["effects"][0]["status"] == "dispatch_reserved"
            assert "effect.reserve" in g.transport_profile()["submit_kinds"]
            assert "effect.authorize" not in g.transport_profile()["submit_kinds"]
        assert g.submit(raw)["status"] == "committed" and len(n.sent) == 1

    asyncio.run(run())


@pytest.mark.parametrize("error", [OSError("private"), Failure("NOT_READY", "fixture")])
def test_state_failure_is_bounded(error):
    n = Node()
    n.live_state = lambda: (_ for _ in ()).throw(error)
    with pytest.raises(Failure):
        n.gateway().inspect()


def test_definitive_rejection_is_not_ambiguous():
    n = Node()
    n.submit = lambda raw: (_ for _ in ()).throw(Failure("REJECTED", "fixture"))
    with pytest.raises(Failure, match="REJECTED"):
        n.gateway().submit(n.envelope())


def test_submission_followed_by_observation_failure_is_unknown():
    n = Node()
    original = n.submit

    def submit(raw):
        original(raw)
        n.live_state = lambda: (_ for _ in ()).throw(OSError("private"))
        return {}

    n.submit = submit
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        n.gateway().submit(n.envelope())


def test_native_cancelled_is_not_unknown():
    n = Node()
    value = n.gateway().inspect("task", n.task)
    value["record"]["status"] = "cancelled"
    assert task_projection(n.task, value).status.state == pb.TASK_STATE_CANCELED
    with pytest.raises(Failure, match="SCOPE"):
        n.gateway().task_target(document(n.envelope("mission.pause").encode())["command"])


def test_candidate_acceptance_is_derived_from_current_committed_registry():
    from test_work_acceptance import prepared

    n = Node()
    n.h = prepared()
    g = n.gateway()
    assert g.inspect("capability", n.h.candidate)["record"]["status"] == "pending"
    for index in range(3):
        n.h.observation(index)
    assert g.inspect("capability", n.h.candidate)["record"]["status"] == "accepted"
    assert n.h.candidate in g.inspect("task", n.h.checks[0])["capabilities"]
    n.h.send("artifact.revoke", {"candidate": n.h.candidate})
    assert g.inspect("capability", n.h.candidate)["record"]["status"] == "revoked"
