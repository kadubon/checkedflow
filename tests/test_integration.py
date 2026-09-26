"""Required Linux qualification. Missing infrastructure is skipped locally, never qualified."""

import concurrent.futures
import os
import platform
import sys
import time
from pathlib import Path

import pytest
from network import FaultNetwork
from patch_fixture import invoice

from checkedflow.core.values import Failure
from checkedflow.distributed.cluster import Cluster
from checkedflow.distributed.demo import bootstrap, run_scenario
from checkedflow.domains.repository_patch import Tree
from checkedflow.repository_execution import observe_patch
from checkedflow.runner import GVisorRunner, Limits


@pytest.fixture
def infrastructure():
    image, binary = os.environ.get("CHECKEDFLOW_IMAGE"), os.environ.get("CHECKEDFLOW_COMETBFT")
    if platform.system() != "Linux" or not image or not binary:
        if os.environ.get("CHECKEDFLOW_REQUIRE_INFRA") == "1":
            pytest.fail("required Linux, pinned image and CometBFT configuration missing")
        pytest.skip("real Linux/gVisor/CometBFT qualification was not requested")
    GVisorRunner(image).check()
    if os.environ.get("CHECKEDFLOW_REQUIRE_INFRA") == "1":
        import checkedflow

        origin = Path(checkedflow.__file__).resolve().relative_to(Path(sys.prefix).resolve())
        assert "site-packages" in origin.parts, "qualification must use the installed wheel"
    return image, binary


@pytest.fixture
def cluster(infrastructure, tmp_path):
    image, binary = infrastructure
    laboratory = Cluster(tmp_path / "network", binary, base_port=29650)
    network = FaultNetwork(laboratory)
    laboratory.fault_network = network
    try:
        laboratory.start()
        yield laboratory, image
    finally:
        laboratory.close()
        network.close()


@pytest.mark.integration
@pytest.mark.qualification
def test_actual_generation_registration_reuse_and_next_generation(cluster, tmp_path):
    from checkedflow.wire import dumps

    laboratory, image = cluster
    report = run_scenario(laboratory, image)
    assert report["status"] == "passed"
    assert len(report["observations"]) == 6
    assert report["missions"][0]["unique_generated_behaviors"] == 3
    assert report["missions"][0]["reuse_calls"] == 2
    assert report["missions"][0]["spent"] == report["missions"][1]["spent"]
    assert report["missions"][0]["reserved"] == 0
    (tmp_path / "acceptance.json").write_bytes(dumps(report))


@pytest.mark.integration
@pytest.mark.qualification
def test_agent_protocols_share_four_node_commit(cluster):
    import asyncio
    import json
    import sys

    import httpx
    from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
    from a2a.types import a2a_pb2 as pb
    from google.protobuf.json_format import MessageToDict, ParseDict
    from mcp import Client as MCPClient
    from mcp import StdioServerParameters

    from checkedflow.agents.a2a import create_app
    from checkedflow.agents.gateway import Gateway
    from checkedflow.identity import sign
    from checkedflow.wire import dumps

    laboratory, image = cluster
    bootstrap(laboratory, image)
    state = laboratory.client().state()
    command = {
        "api_version": "checkedflow/v1",
        "chain": state.chain,
        "id": "agent-command",
        "actor": "w0",
        "nonce": state.nonces.get("w0", 0) + 1,
        "kind": "task.create",
        "payload": {
            "id": "agent-task",
            "mission": "reuse",
            "phase": "generate",
            "spec": {"target": ["double"]},
            "dependencies": [],
            "cost": 256,
            "ttl": 1000,
            "effect": "isolated",
        },
    }
    envelope = dumps(sign(command, {"w0": laboratory.keys["w0"]})).decode()

    async def exchange():
        token = "test-only-four-node-agent-token-000000"
        gateway = Gateway(laboratory.client(), state.chain, "reuse")
        app = create_app(gateway, "http://127.0.0.1/rpc", token)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer " + token},
        ) as transport:
            card = await A2ACardResolver(transport, "http://127.0.0.1").get_agent_card()
            client = ClientFactory(ClientConfig(httpx_client=transport, streaming=False)).create(
                card
            )
            request = ParseDict(
                {
                    "message": {
                        "messageId": "agent-command",
                        "role": "ROLE_USER",
                        "contextId": "reuse",
                        "parts": [{"data": {"operation": "submit", "envelopeJson": envelope}}],
                    }
                },
                pb.SendMessageRequest(),
            )
            responses = [event async for event in client.send_message(request)]
            ack = json.loads(MessageToDict(responses[0].message.parts[0].data)["checkedflowJson"])
            assert ack["status"] == "committed"
            laboratory.wait_height(ack["height"])
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "checkedflow.cli",
                    "mcp",
                    "--rpc",
                    laboratory.client(1).url,
                    "--chain",
                    state.chain,
                    "--mission",
                    "reuse",
                ],
            )
            async with MCPClient(parameters, read_timeout_seconds=40) as mcp:
                observed = await mcp.call_tool(
                    "checkedflow_inspect", {"kind": "task", "identity": "agent-task"}
                )
                assert observed.structured_content["record"]["status"] == "ready"
                repeated = await mcp.call_tool("checkedflow_submit", {"envelope_json": envelope})
                assert not repeated.is_error
            projection = await client.get_task(pb.GetTaskRequest(id="agent-task"))
            assert projection.status.state == pb.TASK_STATE_SUBMITTED

    asyncio.run(exchange())
    laboratory.wait_height(laboratory.client(1).state().height)
    assert all(len(laboratory.client(i).state().tasks) == 1 for i in range(4))
    assert all(laboratory.client(i).state().missions["reuse"].spent == 0 for i in range(4))
    laboratory.common_hash()


@pytest.mark.integration
@pytest.mark.qualification
def test_competing_leases_one_stop_partition_quorum_loss_and_recovery(cluster):
    laboratory, image = cluster
    bootstrap(laboratory, image)
    import grpc

    from checkedflow.distributed.proto.generated.tendermint.abci import types_pb2 as pb
    from checkedflow.distributed.proto.generated.tendermint.abci import types_pb2_grpc as rpc

    with grpc.insecure_channel(f"127.0.0.1:{laboratory.port(0, 2)}") as channel:
        rejected = rpc.ABCIStub(channel).ProcessProposal(
            pb.RequestProcessProposal(
                height=laboratory.client().state().height + 1, txs=[b'{"forged":true}']
            ),
            timeout=5,
        )
        assert rejected.status == pb.ResponseProcessProposal.REJECT
    with pytest.raises(Failure, match="REJECTED"):
        laboratory.client().submit({"forged": True})
    laboratory.send(
        "task.create",
        {
            "id": "race",
            "mission": "reuse",
            "phase": "generate",
            # Its base64 RPC body exceeds CometBFT's default 1,000,000-byte ceiling.
            "spec": {"target": ["double"], "context": ["x" * 250000] * 3},
            "dependencies": [],
            "cost": 256,
            "ttl": 1000,
            "effect": "isolated",
        },
        actor="w0",
    )
    laboratory.wait_height(laboratory.client().state().height)

    def acquire(index):
        try:
            laboratory.send("task.lease", {"id": "race"}, actor=f"w{index}", node=index)
            return True
        except (Failure, TimeoutError):
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        successes = list(executor.map(acquire, (0, 1)))
    assert sum(successes) == 1
    assert laboratory.client().state().missions["reuse"].reserved == 256
    laboratory.stop_node(3, crash=True)
    height = laboratory.client().state().height
    laboratory.wait_height(height + 3, nodes=(0, 1, 2))
    laboratory.start_node(3)
    laboratory.wait_height(height + 4)
    laboratory.fault_network.isolate(3, True)
    try:
        height = laboratory.client().state().height
        laboratory.wait_height(height + 3, nodes=(0, 1, 2))
        laboratory.stop_node(2, crash=True)
        time.sleep(2)
        stalled = laboratory.client().state().height
        time.sleep(3)
        assert laboratory.client().state().height == stalled
    finally:
        laboratory.fault_network.isolate(3, False)
    laboratory.start_node(2)
    laboratory.wait_height(stalled + 3)
    laboratory.common_hash()


@pytest.mark.sandbox
@pytest.mark.qualification
@pytest.mark.parametrize(
    "source, expected",
    [
        ("while True: pass", "timeout"),
        (
            "import sys\nwhile True: sys.stdout.write('x' * 4096); sys.stdout.flush()",
            "output_limit",
        ),
        (
            "import os\nwhile True:\n try: os.fork()\n except OSError: break\nwhile True: pass",
            "process_limit",
        ),
        ("import time\ntime.sleep(30)", "timeout"),
    ],
)
def test_sandbox_resource_exhaustion(infrastructure, source, expected):
    image, _ = infrastructure
    runner = GVisorRunner(image, limits=Limits(seconds=3, output_bytes=8192))
    if expected == "process_limit":
        probe = runner.run(("python", "-c", "print('ready')"), {}, None)
        assert probe.status == "reported" and probe.stdout.strip() == b"ready"
    result = runner.run(("python", "-c", source), {}, "x" * 200000)
    assert result.status == "unknown"
    if expected == "process_limit":
        # Resource containment may terminate the sandbox before its wall-time limit.
        # Docker launch failure (125) and unknown cleanup are never accepted here.
        assert result.reason in {"timeout", "exit_nonzero:2", "exit_nonzero:137"}
    else:
        assert result.reason == expected
    assert len(result.stdout) <= 8192 and len(result.stderr) <= 8192


@pytest.mark.sandbox
@pytest.mark.qualification
def test_sandbox_network_paths_nonroot_and_checker_protection(infrastructure):
    image, _ = infrastructure
    runner = GVisorRunner(image)
    with pytest.raises(Failure, match="PATH"):
        runner.run(("python",), {"../escape": "bad"}, None)
    script = """import os, socket
assert os.getuid() == 65534
for path in ['/work/check.py', '/etc/checkedflow-forbidden']:
    try:
        open(path, 'w').write('changed')
    except OSError:
        pass
    else:
        raise RuntimeError('writable protected path')
s = socket.socket()
s.settimeout(1)
try:
    s.connect(('1.1.1.1', 443))
except OSError:
    print('blocked')
else:
    raise RuntimeError('external network available')
"""
    result = runner.run(("python", "-c", script), {"check.py": "fixed"}, None)
    assert result.status == "reported" and result.stdout.strip() == b"blocked"


@pytest.mark.sandbox
@pytest.mark.qualification
def test_sandbox_nested_repository_tree(infrastructure):
    image, _ = infrastructure
    tree = Tree((("pkg/logic.py", b"def solve(value):\n    return value + 7\n"),))
    script = """import json, os, sys
from pkg.logic import solve
assert os.getuid() == 65534
try:
    open('/work/pkg/logic.py', 'w').write('modified')
except OSError:
    pass
else:
    raise RuntimeError('source was writable')
print(json.dumps(solve(json.load(sys.stdin))))
"""
    result = GVisorRunner(image).run_tree(("python", "-B", "-s", "-c", script), tree, 5)
    assert result.status == "reported" and result.stdout.strip() == b"12"


@pytest.mark.sandbox
@pytest.mark.qualification
@pytest.mark.parametrize(
    "source,expected",
    [
        (None, "cases_match"),
        ("def invoice_total(value): return 0\n", "cases_differ"),
        ("invalid Python syntax!\n", "unknown"),
        ("import sys\nprint('PASS')\nsys.exit(0)\n", "invalid_output"),
        ("import sys\nprint('{\"results\":[]}')\nsys.exit(0)\n", "invalid_output"),
        ("while True: pass\n", "unknown"),
    ],
)
def test_repository_patch_independent_observation(infrastructure, source, expected):
    image, _ = infrastructure
    base, patch, contract, cases = invoice(image, source)
    observation = observe_patch(base, patch, contract, cases, height=10)
    if expected == "unknown":
        assert observation.case_match is None
    else:
        assert observation.reason == expected
        assert observation.case_match is (
            True if expected == "cases_match" else (False if expected == "cases_differ" else None)
        )


@pytest.mark.integration
@pytest.mark.sandbox
@pytest.mark.qualification
def test_v2_consensus_patch_execution_and_crash_recovery(infrastructure, tmp_path):
    import json
    import sqlite3
    from contextlib import closing
    from dataclasses import asdict, replace
    from hashlib import sha256
    from io import BytesIO

    from checkedflow.artifacts import Access, LocalStore
    from checkedflow.core.artifact import Reference
    from checkedflow.distributed.operational_cluster import Cluster as OperationalCluster
    from checkedflow.operational_runtime import Runtime as OperationalRuntime
    from checkedflow.operational_storage import Store as OperationalStore
    from checkedflow.wire import digest, dumps, validate

    image, binary = infrastructure
    cluster = OperationalCluster(tmp_path / "operational", binary)
    try:
        cluster.start()
        base, patch, contract, cases = invoice(image)
        contract = replace(contract, deadline_height=100000)
        target = digest(validate(json.loads(json.dumps(asdict(contract)))))
        cluster.send("budget.configure", {"budget": 100, "verification_reserve": 40})
        cluster.send("mission.resume", {})
        ticket = cluster.send(
            "budget.reserve",
            {
                "phase": "execute",
                "ceiling": 30,
                "target": target,
            },
        )["request"]
        admitted = cluster.send(
            "task.admit",
            {
                "ticket": ticket,
                "workers": ["w0", "w1"],
                "lease_blocks": 1000,
                "expires": cluster.client().state().height + 10000,
                "max_attempts": 2,
            },
        )
        task = admitted["request"]
        cluster.wait_height(int(admitted["receipt"]["height"]))

        def compete(index):
            try:
                cluster.send("task.lease", {"task": task}, actor=f"w{index}", node=index)
                return index, "OK"
            except Failure as exc:
                assert exc.code in {"REJECTED", "OUTCOME_UNKNOWN"}
                return index, exc.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(compete, (0, 1)))
        # A mempool timeout is uncertain, not a rejection. Reconcile committed ownership.
        cluster.wait_height(max(cluster.client(index).state().height for index in range(4)) + 1)
        acquired = cluster.client().state()
        worker = acquired.tasks[0].owner
        assert worker in {"w0", "w1"} and acquired.tasks[0].fence == 1
        owner = int(worker[1])
        assert dict(acquired.journal.actors)[worker] == 1
        assert dict(acquired.journal.actors)[f"w{1 - owner}"] == 0
        assert all(code != "OK" or index == owner for index, code in receipts)
        cluster.send("task.start", {"task": task, "fence": 1}, actor=worker, node=owner)
        cluster.stop_node(3, crash=True)
        state = cluster.client(owner).state()
        assert state.tasks[0].owner == worker and state.tasks[0].status == "running"
        assert state.budget.tickets[0].target == target
        observation = observe_patch(base, patch, contract, cases, height=state.height)
        assert observation.case_match is True and observation.contract_digest == target
        evidence = validate(json.loads(json.dumps(asdict(observation))))
        raw = dumps(evidence)
        reference = Reference(
            "sha256",
            digest(evidence),
            len(raw),
            "application/json",
            "evidence",
            "repository",
            target,
        )
        access = Access(worker, frozenset({"repository"}), frozenset({"read", "write"}))
        artifacts = LocalStore(tmp_path / "evidence.sqlite")
        artifacts.put(reference, BytesIO(raw), access=access)
        assert artifacts.get(reference, access=access) == raw
        finish = cluster.send(
            "task.finish",
            {
                "task": task,
                "fence": 1,
                "outcome": "reported",
                "evidence": reference.digest,
            },
            actor=worker,
            node=owner,
        )
        cluster.wait_height(int(finish["receipt"]["height"]), nodes=(0, 1, 2))
        assert cluster.client().state().budget.spent == 30
        cluster.stop_node(2, crash=True)
        time.sleep(2)
        stopped = cluster.client().state().height
        time.sleep(1.5)
        assert cluster.client().state().height == stopped
        cluster.start_node(2)
        cluster.start_node(3)
        cluster.wait_height(stopped + 3)
        states = [cluster.client(index).state() for index in range(4)]
        assert all(
            state.tasks == states[0].tasks and state.budget == states[0].budget for state in states
        )
        assert states[0].tasks[0].status == "finished" and states[0].budget.spent == 30
        height, app_hash = cluster.common_hash()
        assert height >= int(finish["receipt"]["height"]) and len(app_hash) == 64
        cluster.close()
        for index in range(4):
            path = cluster.directory / f"node{index}" / "operational.sqlite"
            store = OperationalStore(path, cluster.initial)
            final = store.load()
            assert store.verify_history(expected_hash=OperationalRuntime(final).state_hash) == final
            with closing(sqlite3.connect(path)) as db:
                body, fingerprint = db.execute(
                    "SELECT body, hash FROM blocks WHERE height=?", (height,)
                ).fetchone()
            assert sha256(body).hexdigest() == fingerprint
            assert json.loads(body)["state_hash"] == app_hash.lower()
    finally:
        cluster.close()
