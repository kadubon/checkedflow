"""Required Linux qualification. Missing infrastructure is skipped locally, never qualified."""

import concurrent.futures
import os
import subprocess
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
def infrastructure(runtime_infrastructure, tmp_path, monkeypatch):
    from checkedflow.sandbox_recovery import Docker, Recovery, boot_id

    image, binary = runtime_infrastructure
    directory = tmp_path / "sandbox-recovery"
    recovery = Recovery(directory, Docker(), boot_id())
    monkeypatch.setenv("CHECKEDFLOW_SANDBOX_RECOVERY", str(directory))
    with (tmp_path / "recovery.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "checkedflow.sandbox_recovery", str(directory)],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                assert process.poll() is None, "independent recovery service stopped"
                try:
                    recovery.ready()
                    break
                except Failure:
                    assert time.monotonic() < deadline, "recovery readiness timeout"
                    time.sleep(0.1)
            yield image, binary
        finally:
            import sqlite3

            try:
                recovery.sweep()
                with sqlite3.connect(directory / "sandbox.sqlite") as db:
                    pending = db.execute("SELECT name FROM containers").fetchall()
                for (name,) in pending:
                    assert recovery.cleanup(name), "unresolved laboratory container creation"
            finally:
                process.terminate()
                process.wait(timeout=15)


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


@pytest.mark.sandbox
@pytest.mark.qualification
@pytest.mark.parametrize("phase", ["created", "running"])
def test_independent_reaper_survives_worker_process_death(infrastructure, tmp_path, phase):
    import sqlite3

    from checkedflow.sandbox_recovery import Docker

    image, _ = infrastructure
    directory = Path(os.environ["CHECKEDFLOW_SANDBOX_RECOVERY"])
    script = r"""
import os, sys
from checkedflow.runner import GVisorRunner, Limits
from checkedflow.sandbox_recovery import Recovery
if sys.argv[2] == "created":
    Recovery.bind = lambda *args: os._exit(39)
GVisorRunner(sys.argv[1], limits=Limits(seconds=8)).run(
    ("python", "-c", "while True: pass"), {}, None
)
"""
    engine = Docker()
    with (tmp_path / "crashed-worker.log").open("wb") as log:
        worker = subprocess.Popen(
            [sys.executable, "-c", script, image, phase], stdout=log, stderr=log
        )
        try:
            deadline = time.monotonic() + 20
            container = None
            while container is None:
                with sqlite3.connect(directory / "sandbox.sqlite") as db:
                    row = db.execute("SELECT name FROM containers").fetchone()
                info = None if row is None else engine.inspect(row[0])
                if info is not None and (phase == "created" or info["State"]["Running"]):
                    container = info["Id"]
                    assert info["HostConfig"]["Runtime"] == "runsc"
                    if phase == "created":
                        assert not info["State"]["Running"]
                    break
                assert time.monotonic() < deadline, "real container did not reach failure window"
                time.sleep(0.1)
            if phase == "running":
                worker.kill()
            worker.wait(timeout=10)
            assert worker.returncode == (39 if phase == "created" else -9)
            while True:
                try:
                    absent = engine.inspect(container) is None
                except Failure as exc:
                    # Removal can race Docker's list-then-inspect read. Unknown is not absence.
                    assert exc.code == "CLEANUP_UNKNOWN"
                    absent = False
                with sqlite3.connect(directory / "sandbox.sqlite") as db:
                    retired = db.execute("SELECT COUNT(*) FROM containers").fetchone()[0] == 0
                if absent and retired:
                    break
                assert time.monotonic() < deadline, "independent container recovery timed out"
                time.sleep(0.1)
        finally:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=10)


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
@pytest.mark.qualification
def test_v2_inherited_accounting_commits_and_recovers_on_four_nodes(infrastructure, tmp_path):
    import json
    from importlib.resources import files

    from checkedflow.distributed.operational_application import Configuration
    from checkedflow.distributed.operational_cluster import Cluster as OperationalCluster
    from checkedflow.legacy_inventory import Checkpoint
    from checkedflow.legacy_successor import prepare
    from checkedflow.operational_runtime import Runtime
    from checkedflow.operational_storage import Store
    from checkedflow.wire import dumps

    _, binary = infrastructure
    legacy = json.loads(files("checkedflow").joinpath("data/legacy-v1.json").read_text())
    old = legacy["final_state"]
    checkpoint = Checkpoint(old["chain"], old["height"], legacy["final_state_hash"])
    cluster = OperationalCluster(tmp_path / "inherited", binary)
    # Prepare only unused laboratory genesis files; never rewrite an active chain.
    cluster.initial = prepare(dumps(old), checkpoint, cluster.initial, mission="m")
    cluster.configuration = Configuration(cluster.initial, cluster.configuration.validators)
    encoded = cluster.configuration.encode()
    for name in ("operational.json", "genesis.json"):
        (cluster.directory / name).write_bytes(dumps(encoded))
    for index in range(4):
        path = cluster.directory / f"node{index}" / "config/genesis.json"
        value = json.loads(path.read_text())
        value["app_state"] = encoded
        path.write_text(json.dumps(value), encoding="utf-8")
    initial_budget = cluster.initial.budget
    assert initial_budget.spent > 0 and old["tasks"]["unknown"]["status"] == "uncertain"
    try:
        cluster.start()
        cluster.send("mission.resume", {})
        ticket = cluster.send(
            "budget.reserve",
            {
                "phase": "verify",
                "ceiling": 1,
                "target": "a" * 64,
            },
        )["request"]
        cluster.send("budget.settle", {"ticket": ticket, "outcome": "unknown", "charged": 1})
        cluster.send("mission.pause", {})
        cluster.send("journal.rollover", {})
        cluster.wait_height(cluster.client().state().height)
        cluster.processes.stop_node(0, crash=True)
        cluster.start_node(0)
        cluster.wait_height(cluster.client(1).state().height)
        for index in range(4):
            state = cluster.client(index).state()
            assert state.budget.inheritance == initial_budget.inheritance
            assert state.budget.spent == initial_budget.spent + 1
            assert state.budget.reserved == initial_budget.reserved
            assert state.budget.available == initial_budget.available - 1
        _, common = cluster.common_hash()
        assert common
    finally:
        cluster.close()
    for index in range(4):
        store = Store(cluster.directory / f"node{index}" / "operational.sqlite", cluster.initial)
        state = store.load()
        assert store.verify_history(expected_hash=Runtime(state).state_hash) == state
        assert state.budget.inheritance == initial_budget.inheritance


@pytest.mark.integration
@pytest.mark.sandbox
@pytest.mark.qualification
def test_v2_consensus_patch_execution_and_crash_recovery(infrastructure, tmp_path):
    import asyncio
    import json
    import sqlite3
    from contextlib import closing
    from dataclasses import asdict, replace
    from hashlib import sha256
    from io import BytesIO

    from checkedflow.artifacts import Access, LocalStore
    from checkedflow.core.artifact import Reference
    from checkedflow.core.work_acceptance import status as acceptance_status
    from checkedflow.dispatch_watchdog import Watchdog
    from checkedflow.distributed.operational_cluster import Cluster as OperationalCluster
    from checkedflow.operational_runtime import Runtime as OperationalRuntime
    from checkedflow.operational_storage import Store as OperationalStore
    from checkedflow.repository_reuse import Inputs, prepare, tree_bytes
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
        from checkedflow.repository_worker import EvidencePublisher, RepositoryExecutor
        from checkedflow.worker_schedule import Policy, Schedule
        from checkedflow.worker_submission import Coordinator
        from checkedflow.worker_supervisor import Supervisor

        own = cluster.client(owner)
        from checkedflow.agents.operational_gateway import Gateway as OperationalGateway

        agent_gateway = OperationalGateway(own, cluster.initial.chain, cluster.initial.mission)
        from checkedflow.agents.access import LOCAL
        from checkedflow.agents.access import Policy as ClientPolicy
        from checkedflow.wire import dumps

        client_policy_path = tmp_path / "client-policy.json"
        client_policy_path.write_bytes(
            dumps(
                {
                    "profile": "checkedflow/access-policy/v1",
                    "grants": [
                        {
                            "issuer": LOCAL.issuer,
                            "client": LOCAL.client,
                            "subject": LOCAL.subject,
                            "chain": cluster.initial.chain,
                            "mission": cluster.initial.mission,
                            "roles": ["inspect", "submit"],
                            "actors": [worker],
                        }
                    ],
                }
            )
        )
        client_policy = ClientPolicy(
            client_policy_path, cluster.initial.chain, cluster.initial.mission
        )
        access = Access(worker, frozenset({"repository"}), frozenset({"read", "write"}))
        artifacts = LocalStore(tmp_path / "evidence.sqlite")
        executor = RepositoryExecutor(base, patch, contract, cases)
        executed = []

        def confirmed_submit(raw):
            from mcp import Client as MCPClient

            from checkedflow.agents.mcp import create_server

            async def through_mcp():
                async with MCPClient(create_server(agent_gateway, policy=client_policy)) as client:
                    response = await client.call_tool(
                        "checkedflow_submit", {"envelope_json": raw.decode("utf-8")}
                    )
                    assert not response.is_error, response.structured_content
                    return response.structured_content

            reply = asyncio.run(through_mcp())
            cluster.wait_height(int(reply["height"]), nodes=(owner,))
            return reply

        coordinator = Coordinator(
            tmp_path / "worker-commands",
            own.live_state,
            confirmed_submit,
            cluster.keys[(worker, 1)],
            chain=cluster.initial.chain,
            mission=cluster.initial.mission,
            actor=worker,
            revision=1,
        )
        worker_watchdog = Watchdog(
            own.live_state,
            chain=cluster.initial.chain,
            mission=cluster.initial.mission,
            max_read_age_ns=5_000_000_000,
            max_stall_ns=5_000_000_000,
        )
        sample = worker_watchdog.poll()
        cluster.wait_height(sample.height + 1)

        def supervised_observation(state, attempt):
            cluster.stop_node(3, crash=True)
            supervisor.heartbeat(attempt.identity)
            result = executor(state, attempt)
            executed.append(result)
            return result

        supervisor = Supervisor(
            tmp_path / "worker-execution",
            coordinator,
            worker_watchdog,
            supervised_observation,
            EvidencePublisher(artifacts, access),
        )
        schedule = Schedule(
            tmp_path / "worker-schedule",
            supervisor,
            Policy((task,), duration_seconds=120),
            clock_epoch=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        )
        assert schedule.run(max_polls=256).mode == "complete"
        assert 1 <= schedule.tick().attempts <= 8
        assert len(executed) == 1 and executed[0].outcome == "reported"
        assert (
            Supervisor(
                tmp_path / "worker-execution",
                coordinator,
                worker_watchdog,
                supervised_observation,
                EvidencePublisher(artifacts, access),
            ).step(task)
            == "finished"
        )
        assert len(executed) == 1
        from checkedflow.agents.a2a import Handler

        async def a2a_observe():
            from google.protobuf.json_format import MessageToDict

            from checkedflow.agents.journal import Journal
            from checkedflow.agents.secrets import Keyring

            keys = Keyring.ephemeral()
            journal_path = tmp_path / "agent-observations.sqlite"
            handler = Handler(
                agent_gateway, journal=Journal(agent_gateway, journal_path, keyring=keys)
            )
            try:
                await handler.refresh()
                projected = handler.project(task)
                assert projected.id == task and len(projected.artifacts) == 1
                part = MessageToDict(projected.artifacts[0].parts[0])
                assert "not_implied" in part["data"]["receiptJson"]
                # Exercise installed-byte custody without contacting any external callback.
                configuration = {"id": "fixture", "token": "installed-fixture-secret"}
                handler.journal.put_config(task, "fixture", configuration)
                stored = handler.journal.db.execute("SELECT value FROM notifications").fetchone()[0]
                assert b"installed-fixture-secret" not in stored
            finally:
                handler.journal.close()
            restored = Journal(agent_gateway, journal_path, keyring=keys)
            try:
                assert restored.configurations(task) == [configuration]
            finally:
                restored.close()

        asyncio.run(a2a_observe())
        raw = executed[0].evidence
        evidence = json.loads(raw)
        assert evidence["case_match"] is True and evidence["contract_digest"] == target
        reference = Reference(
            "sha256",
            sha256(raw).hexdigest(),
            len(raw),
            "application/json",
            "evidence",
            "repository",
            target,
        )
        assert artifacts.get(reference, access=access) == raw
        cluster.wait_height(own.state().height, nodes=(0, 1, 2))
        assert cluster.client().state().budget.spent == 30
        watchdog = Watchdog(
            cluster.client().live_state,
            chain=cluster.initial.chain,
            mission=cluster.initial.mission,
            max_read_age_ns=5_000_000_000,
            max_stall_ns=2_000_000_000,
        )

        def warm_watchdog():
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    watchdog.poll()
                    return watchdog.current()
                except Failure as exc:
                    assert exc.code in {"NOT_READY", "STALE"}
                time.sleep(0.1)
            raise AssertionError("own-node watchdog did not observe fresh height progress")

        assert warm_watchdog().mode == "running"
        cluster.stop_node(2, crash=True)
        time.sleep(2)
        stopped = cluster.client().state().height
        # Successful queries of the same state cannot renew dispatch readiness during quorum loss.
        for _ in range(6):
            time.sleep(0.4)
            assert watchdog.poll().height == stopped
            with pytest.raises(Failure, match="NOT_READY"):
                watchdog.current()
        assert cluster.client().state().height == stopped
        cluster.start_node(2)
        cluster.start_node(3)
        cluster.wait_height(stopped + 3)
        assert warm_watchdog().height > stopped
        watchdog.stop()
        with pytest.raises(Failure, match="STOPPED"):
            watchdog.current()
        states = [cluster.client(index).state() for index in range(4)]
        assert all(
            state.tasks == states[0].tasks and state.budget == states[0].budget for state in states
        )
        assert states[0].tasks[0].status == "finished" and states[0].budget.spent == 30
        checks = []
        for index in range(4):
            reservation = cluster.send(
                "budget.reserve", {"phase": "verify", "ceiling": 10, "target": target}
            )["request"]
            checks.append(
                cluster.send(
                    "task.admit",
                    {
                        "ticket": reservation,
                        "workers": [f"v{index}"],
                        "lease_blocks": 1000,
                        "expires": 100000,
                        "max_attempts": 1,
                    },
                )["request"]
            )
        candidate = cluster.send(
            "artifact.admit",
            {
                "target": target,
                "artifact": contract.result_tree,
                "expires": contract.deadline_height,
                "checks": checks,
            },
        )
        cluster.wait_height(int(candidate["receipt"]["height"]))
        stored_inputs = []
        for kind, body in (
            ("source-tree", tree_bytes(base)),
            ("patch", patch),
            ("evidence", cases),
        ):
            stored = replace(
                reference, digest=sha256(body).hexdigest(), length=len(body), kind=kind
            )
            artifacts.put(stored, BytesIO(body), access=access)
            stored_inputs.append(stored)
        verifier_refs = []
        for index, check in enumerate(checks):
            verifier = f"v{index}"
            cluster.send("task.lease", {"task": check}, actor=verifier, node=index)
            cluster.send("task.start", {"task": check, "fence": 1}, actor=verifier, node=index)
            checked = observe_patch(
                base, patch, contract, cases, height=cluster.client(index).state().height
            )
            assert checked.case_match is True and checked.contract_digest == target
            checked_bytes = dumps(validate(json.loads(json.dumps(asdict(checked)))))
            checked_ref = replace(
                reference, digest=sha256(checked_bytes).hexdigest(), length=len(checked_bytes)
            )
            verifier_access = Access(
                verifier, frozenset({"repository"}), frozenset({"read", "write"})
            )
            artifacts.put(checked_ref, BytesIO(checked_bytes), access=verifier_access)
            verifier_refs.append(checked_ref)
            assert artifacts.get(checked_ref, access=verifier_access) == checked_bytes
            cluster.send(
                "task.finish",
                {"task": check, "fence": 1, "outcome": "reported", "evidence": checked_ref.digest},
                actor=verifier,
                node=index,
            )
            attested = cluster.send(
                "artifact.attest",
                {
                    "candidate": candidate["request"],
                    "task": check,
                    "evidence": checked_ref.digest,
                    "verdict": "pass",
                },
                actor=verifier,
                node=index,
            )
            cluster.wait_height(int(attested["receipt"]["height"]))
            observed = cluster.client(index).state()
            assert acceptance_status(
                observed.candidates[0], observed.credentials, observed.height
            ) == ("accepted" if index >= 2 else "pending")
            assert observed.budget.spent == 30 + 10 * (index + 1)
            assert observed.budget.reserved == 10 * (3 - index)
        reusable = Inputs(*stored_inputs, tuple(verifier_refs))
        prepared = prepare(
            observed, candidate["request"], contract, reusable, artifacts, access=access
        )
        assert prepared.tree.digest == contract.result_tree
        assert prepared.state_hash == OperationalRuntime(observed).state_hash
        with pytest.raises(Failure, match="BINDING"):
            prepare(
                observed,
                candidate["request"],
                replace(contract, base_commit="f" * 40),
                reusable,
                artifacts,
                access=access,
            )
        withdrawn = cluster.send(
            "artifact.withdraw",
            {
                "candidate": candidate["request"],
                "task": checks[3],
            },
            actor="v3",
            node=3,
        )
        cluster.wait_height(int(withdrawn["receipt"]["height"]) + 1)
        states = [cluster.client(index).state() for index in range(4)]
        assert all(state.candidates == states[0].candidates for state in states)
        assert all(
            acceptance_status(state.candidates[0], state.credentials, state.height) == "quarantined"
            and state.budget.spent == 70
            and state.budget.reserved == 0
            for state in states
        )
        with pytest.raises(Failure, match="ACCEPTANCE"):
            prepare(states[0], candidate["request"], contract, reusable, artifacts, access=access)
        paused = cluster.send("mission.pause", {})
        cluster.wait_height(int(paused["receipt"]["height"]) + 1)
        assert supervisor.retire((task,)) == 1
        assert supervisor.retire((task,)) == 0
        assert artifacts.get(reference, access=access) == raw and len(executed) == 1
        cluster.send("artifact.revoke", {"candidate": candidate["request"]})
        cluster.send("journal.rollover", {})
        prior = cluster.client(0).state()
        retired = cluster.send(
            "history.archive",
            {
                "expected_root": prior.history.root,
                "tickets": sorted(ticket.identity for ticket in prior.budget.tickets),
                "tasks": sorted(task.identity for task in prior.tasks),
                "candidates": [candidate["request"]],
            },
        )
        cluster.wait_height(int(retired["receipt"]["height"]) + 1)
        states = [cluster.client(index).state() for index in range(4)]
        assert all(
            state.history == states[0].history
            and state.history.sequence == 1
            and state.budget.archived_spent == 70
            and state.budget.archived_verification == 40
            and not state.budget.tickets
            and not state.tasks
            and not state.candidates
            for state in states
        )
        height, app_hash = cluster.common_hash()
        assert height >= int(retired["receipt"]["height"]) and len(app_hash) == 64
        cluster.close()
        from checkedflow.operational_backup import export_history, restore_history

        for index in range(4):
            path = cluster.directory / f"node{index}" / "operational.sqlite"
            store = OperationalStore(path, cluster.initial)
            final = store.load()
            assert store.verify_history(expected_hash=OperationalRuntime(final).state_hash) == final
            backup_output = BytesIO()
            checkpoint = export_history(
                store, backup_output, expected_hash=OperationalRuntime(final).state_hash
            )
            restored = restore_history(
                BytesIO(backup_output.getvalue()),
                tmp_path / f"restored-application-{index}",
                initial=cluster.initial,
                checkpoint=checkpoint,
                current_height=final.height,
            )
            assert restored.load() == final
            assert restored.verify_history(expected_hash=checkpoint.state_hash) == final
            assert restored.work_archive(1, expected_root=final.history.root) == store.work_archive(
                1, expected_root=final.history.root
            )

            with closing(sqlite3.connect(path)) as db:
                body, fingerprint = db.execute(
                    "SELECT body, hash FROM blocks WHERE height=?", (height,)
                ).fetchone()
            assert sha256(body).hexdigest() == fingerprint
            assert json.loads(body)["state_hash"] == app_hash.lower()
    finally:
        cluster.close()


@pytest.mark.integration
@pytest.mark.sandbox
@pytest.mark.qualification
def test_v2_changed_base_requires_fresh_funded_verification(infrastructure, tmp_path):
    import json
    import subprocess
    from dataclasses import asdict, replace
    from io import BytesIO

    from patch_fixture import successor

    from checkedflow.artifacts import Access, LocalStore
    from checkedflow.core.artifact import Reference
    from checkedflow.distributed.operational_cluster import Cluster as OperationalCluster
    from checkedflow.domains.repository_patch import digest_bytes
    from checkedflow.repository_reuse import Inputs, contract_digest, prepare, tree_bytes
    from checkedflow.wire import dumps, validate

    image, binary = infrastructure
    cluster = OperationalCluster(tmp_path / "requalification", binary)
    store = LocalStore(tmp_path / "objects.sqlite")
    access = Access("reviewer", frozenset({"repository"}), frozenset({"read", "write"}))
    base, patch, original, cases = invoice(image)
    original = replace(original, deadline_height=100000)
    changed, renewed, commit = successor(base, patch, original)
    # Hashing inert fixture commit bytes neither executes candidate code nor writes Git objects.
    assert (
        subprocess.run(
            ["git", "hash-object", "-t", "commit", "--stdin"],
            input=commit,
            capture_output=True,
            check=True,
            timeout=10,
        )
        .stdout.strip()
        .decode()
        == renewed.base_commit
    )
    try:
        cluster.start()
        cluster.send("budget.configure", {"budget": 100, "verification_reserve": 80})
        cluster.send("mission.resume", {})
        previous = None
        for round_number, (tree, contract) in enumerate(((base, original), (changed, renewed))):
            target = contract_digest(contract)
            refs = []

            def put(body, kind, target=target):
                ref = Reference(
                    "sha256",
                    digest_bytes(body),
                    len(body),
                    "application/json",
                    kind,
                    "repository",
                    target,
                )
                store.put(ref, BytesIO(body), access=access)
                return ref

            inputs = [
                put(tree_bytes(tree), "source-tree"),
                put(patch, "patch"),
                put(cases, "evidence"),
            ]
            if previous is not None:
                old_candidate, old_inputs = previous
                with pytest.raises(Failure, match="BINDING"):
                    prepare(
                        cluster.client().state(),
                        old_candidate,
                        contract,
                        old_inputs,
                        store,
                        access=access,
                    )
            checks = []
            for index in range(4):
                ticket = cluster.send(
                    "budget.reserve", {"phase": "verify", "ceiling": 10, "target": target}
                )["request"]
                checks.append(
                    cluster.send(
                        "task.admit",
                        {
                            "ticket": ticket,
                            "workers": [f"v{index}"],
                            "lease_blocks": 1000,
                            "expires": 100000,
                            "max_attempts": 1,
                        },
                    )["request"]
                )
            admitted = cluster.send(
                "artifact.admit",
                {
                    "target": target,
                    "artifact": contract.result_tree,
                    "expires": contract.deadline_height,
                    "checks": checks,
                },
            )
            candidate = admitted["request"]
            cluster.wait_height(int(admitted["receipt"]["height"]))
            with pytest.raises(Failure, match="ACCEPTANCE"):
                prepare(
                    cluster.client().state(),
                    candidate,
                    contract,
                    Inputs(*inputs, ()),
                    store,
                    access=access,
                )
            for index, check in enumerate(checks):
                actor = f"v{index}"
                cluster.send("task.lease", {"task": check}, actor=actor, node=index)
                cluster.send("task.start", {"task": check, "fence": 1}, actor=actor, node=index)
                observation = observe_patch(
                    tree, patch, contract, cases, height=cluster.client(index).state().height
                )
                assert observation.case_match is True and observation.contract_digest == target
                ref = put(dumps(validate(json.loads(json.dumps(asdict(observation))))), "evidence")
                refs.append(ref)
                cluster.send(
                    "task.finish",
                    {"task": check, "fence": 1, "outcome": "reported", "evidence": ref.digest},
                    actor=actor,
                    node=index,
                )
                attested = cluster.send(
                    "artifact.attest",
                    {
                        "candidate": candidate,
                        "task": check,
                        "evidence": ref.digest,
                        "verdict": "pass",
                    },
                    actor=actor,
                    node=index,
                )
                cluster.wait_height(int(attested["receipt"]["height"]))
            reusable = Inputs(*inputs, tuple(refs))
            for index in range(4):
                state = cluster.client(index).state()
                prepared = prepare(state, candidate, contract, reusable, store, access=access)
                assert prepared.tree.digest == contract.result_tree
                assert state.budget.spent == (round_number + 1) * 40 and state.budget.reserved == 0
            previous = candidate, reusable
        height, app_hash = cluster.common_hash()
        assert height > 0 and len(app_hash) == 64
    finally:
        cluster.close()


@pytest.mark.integration
@pytest.mark.qualification
def test_v2_effect_reservation_expiry_reconciliation_and_replica_recovery(infrastructure, tmp_path):
    """Actual four-node authority/accounting; provider observation below is a signed fixture."""
    import json
    from dataclasses import asdict, replace

    from checkedflow.distributed.operational_cluster import Cluster as OperationalCluster
    from checkedflow.domains.repository_patch import apply_patch, digest_bytes
    from checkedflow.git_tree import tree_id
    from checkedflow.github_effects import Intent, reserved_plan
    from checkedflow.operational_runtime import Runtime as OperationalRuntime
    from checkedflow.operational_storage import Store as OperationalStore
    from checkedflow.repository_reuse import contract_digest
    from checkedflow.wire import dumps, validate

    image, binary = infrastructure
    cluster = OperationalCluster(tmp_path / "effects", binary)
    base, patch, contract, cases = invoice(image)
    contract = replace(contract, allow_draft_pr=True, deadline_height=100000)
    intent = Intent(
        contract.repository,
        42,
        "main",
        contract.base_commit,
        "b" * 40,
        tree_id(apply_patch(base, patch, contract)),
        contract.result_tree,
        contract_digest(contract),
        contract.patch_digest,
    )
    try:
        cluster.start()
        cluster.send("budget.configure", {"budget": 100, "verification_reserve": 40})
        cluster.send("mission.resume", {})
        checks = []
        for index in range(4):
            ticket = cluster.send(
                "budget.reserve", {"phase": "verify", "ceiling": 10, "target": intent.target}
            )["request"]
            checks.append(
                cluster.send(
                    "task.admit",
                    {
                        "ticket": ticket,
                        "workers": [f"v{index}"],
                        "lease_blocks": 1000,
                        "expires": 100000,
                        "max_attempts": 1,
                    },
                )["request"]
            )
        candidate = cluster.send(
            "artifact.admit",
            {
                "target": intent.target,
                "artifact": intent.result,
                "expires": 100000,
                "checks": checks,
            },
        )["request"]
        for index, task in enumerate(checks):
            actor = f"v{index}"
            cluster.send("task.lease", {"task": task}, actor=actor, node=index)
            cluster.send("task.start", {"task": task, "fence": 1}, actor=actor, node=index)
            observation = observe_patch(
                base, patch, contract, cases, height=cluster.client(index).state().height
            )
            assert observation.case_match is True and observation.contract_digest == intent.target
            evidence = digest_bytes(dumps(validate(json.loads(json.dumps(asdict(observation))))))
            cluster.send(
                "task.finish",
                {"task": task, "fence": 1, "outcome": "reported", "evidence": evidence},
                actor=actor,
                node=index,
            )
            cluster.send(
                "artifact.attest",
                {"candidate": candidate, "task": task, "evidence": evidence, "verdict": "pass"},
                actor=actor,
                node=index,
            )
        funding = cluster.send(
            "budget.reserve", {"phase": "execute", "ceiling": 10, "target": intent.digest}
        )["request"]
        effect = cluster.send(
            "effect.prepare",
            {
                "candidate": candidate,
                "ticket": funding,
                "intent": intent.digest,
                "policy": "e" * 64,
                "executor": "e0",
                "revision": 1,
                "expires": 100000,
                "lease_blocks": 20,
            },
        )["request"]
        cluster.send("effect.authorize", {"effect": effect})
        receipt = cluster.send("effect.reserve", {"effect": effect}, actor="e0")
        cluster.wait_height(int(receipt["receipt"]["height"]))
        state = cluster.client().state()
        selected = reserved_plan(
            state, effect, intent, contract, executor="e0", revision=1, policy="e" * 64
        )
        assert selected.operation == state.effects[0].operation
        assert state.budget.spent == 50 and state.budget.reserved == 0
        until = state.effects[0].until
        cluster.stop_node(0, crash=True)
        cluster.wait_height(until + 1, nodes=(1, 2, 3))
        for index in (1, 2, 3):
            state = cluster.client(index).state()
            assert state.effects[0].status == "unknown" and state.budget.spent == 50
            with pytest.raises(Failure):
                reserved_plan(
                    state, effect, intent, contract, executor="e0", revision=1, policy="e" * 64
                )
        cluster.start_node(0)
        cluster.wait_height(until + 2)
        # Applied height alone does not mean the restarted node has left catch-up.
        # Probe readiness without sending anything, then exercise rejection on a continuously
        # running node. An ambiguous broadcast response is never accepted as rejection evidence.
        deadline = time.monotonic() + 45
        while True:
            try:
                recovered = cluster.client(0).live_state()
                assert recovered.effects[0].status == "unknown"
                break
            except Failure:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        with pytest.raises(Failure, match="REJECTED"):
            cluster.send("effect.reserve", {"effect": effect}, actor="e0", node=1)
        # This is an explicit governed observation fixture, not a GitHub call or proof of truth.
        reconciled = cluster.send(
            "effect.reconcile",
            {"effect": effect, "outcome": "observed", "number": 7, "evidence": "f" * 64},
            node=1,
        )
        cluster.wait_height(int(reconciled["receipt"]["height"]))
        assert all(cluster.client(i).state().effects[0].status == "reconciled" for i in range(4))
        withdrawn = cluster.send(
            "artifact.withdraw", {"candidate": candidate, "task": checks[0]}, actor="v0", node=1
        )
        cluster.wait_height(int(withdrawn["receipt"]["height"]))
        for index in range(4):
            state = cluster.client(index).state()
            assert state.effects[0].status == "compensation_required"
            assert state.effects[0].number == 7 and state.budget.spent == 50
        cluster.send("mission.pause", {}, node=1)
        with pytest.raises(Failure, match="REJECTED"):
            cluster.send("mission.resume", {}, node=1)
        height, app_hash = cluster.common_hash()
        assert height > 0 and len(app_hash) == 64
    finally:
        cluster.close()
    for index in range(4):
        store = OperationalStore(
            cluster.directory / f"node{index}" / "operational.sqlite", cluster.initial
        )
        durable = store.load()
        assert store.verify_history(expected_hash=OperationalRuntime(durable).state_hash) == durable
        assert durable.effects[0].status == "compensation_required"
        assert durable.budget.spent == 50


@pytest.mark.integration
@pytest.mark.sandbox
@pytest.mark.qualification
def test_v2_supervised_effect_report_recovery_with_verified_artifacts(
    infrastructure, tmp_path, monkeypatch
):
    """Real consensus and gVisor; the fixed provider response is explicitly a protocol fixture."""
    from dataclasses import asdict, replace
    from io import BytesIO

    from test_github_drafts import pull

    from checkedflow.artifacts import Access, LocalStore
    from checkedflow.core.artifact import Reference
    from checkedflow.dispatch_watchdog import Watchdog
    from checkedflow.distributed.operational_cluster import Cluster as OperationalCluster
    from checkedflow.domains.repository_patch import apply_patch, digest_bytes
    from checkedflow.effect_dispatch import Dispatcher, Policy
    from checkedflow.effect_supervisor import Supervisor
    from checkedflow.git_staging import staging_commit
    from checkedflow.git_tree import tree_id
    from checkedflow.github_drafts import Drafts, Token
    from checkedflow.github_effects import Intent
    from checkedflow.replicated_artifacts import Replica, ReplicatedStore
    from checkedflow.repository_reuse import Inputs, contract_digest, tree_bytes
    from checkedflow.wire import digest, document, dumps, validate
    from checkedflow.worker_submission import Coordinator

    image, binary = infrastructure
    cluster = OperationalCluster(tmp_path / "supervised-effects", binary)
    objects = ReplicatedStore(
        tuple(
            Replica(f"operator-{i}", LocalStore(tmp_path / f"objects-{i}.sqlite")) for i in range(4)
        )
    )
    access = Access("executor", frozenset({"repository"}), frozenset({"read", "write"}))
    base, patch, contract, cases = invoice(image)
    contract = replace(contract, allow_draft_pr=True, deadline_height=100000)
    target = contract_digest(contract)
    intent = Intent(
        contract.repository,
        42,
        "main",
        contract.base_commit,
        staging_commit(contract, apply_patch(base, patch, contract))[0],
        tree_id(apply_patch(base, patch, contract)),
        contract.result_tree,
        target,
        contract.patch_digest,
    )

    def put(raw, kind):
        ref = Reference(
            "sha256", digest_bytes(raw), len(raw), "application/json", kind, "repository", target
        )
        objects.put(ref, BytesIO(raw), access=access)
        return ref

    base_ref, patch_ref, inventory_ref = (
        put(tree_bytes(base), "source-tree"),
        put(patch, "patch"),
        put(cases, "evidence"),
    )
    try:
        cluster.start()
        cluster.send("budget.configure", {"budget": 100, "verification_reserve": 40})
        cluster.send("mission.resume", {})
        checks = []
        for index in range(4):
            ticket = cluster.send(
                "budget.reserve", {"phase": "verify", "ceiling": 10, "target": target}
            )["request"]
            checks.append(
                cluster.send(
                    "task.admit",
                    {
                        "ticket": ticket,
                        "workers": [f"v{index}"],
                        "lease_blocks": 1000,
                        "expires": 100000,
                        "max_attempts": 1,
                    },
                )["request"]
            )
        candidate = cluster.send(
            "artifact.admit",
            {"target": target, "artifact": intent.result, "expires": 100000, "checks": checks},
        )["request"]
        refs = []
        for index, task in enumerate(checks):
            actor = f"v{index}"
            cluster.send("task.lease", {"task": task}, actor=actor, node=index)
            cluster.send("task.start", {"task": task, "fence": 1}, actor=actor, node=index)
            observed = observe_patch(
                base, patch, contract, cases, height=cluster.client(index).state().height
            )
            assert observed.case_match is True
            ref = put(dumps(validate(asdict(observed))), "evidence")
            refs.append(ref)
            cluster.send(
                "task.finish",
                {"task": task, "fence": 1, "outcome": "reported", "evidence": ref.digest},
                actor=actor,
                node=index,
            )
            cluster.send(
                "artifact.attest",
                {"candidate": candidate, "task": task, "evidence": ref.digest, "verdict": "pass"},
                actor=actor,
                node=index,
            )
        policy = {
            "profile": "checkedflow/effect-policy/v2",
            "staging": True,
            "chain": cluster.initial.chain,
            "mission": "repository",
            "repository": intent.repository,
            "repository_id": 42,
            "actor": "owner",
            "executor": "e0",
            "revision": 1,
            "enabled": True,
            "intents": [intent.digest],
        }
        path = tmp_path / "policy.json"
        path.write_bytes(dumps(policy))
        ticket = cluster.send(
            "budget.reserve", {"phase": "execute", "ceiling": 10, "target": intent.digest}
        )["request"]
        identity = cluster.send(
            "effect.prepare",
            {
                "candidate": candidate,
                "ticket": ticket,
                "intent": intent.digest,
                "policy": digest(policy),
                "executor": "e0",
                "revision": 1,
                "expires": 100000,
                "lease_blocks": 200,
            },
        )["request"]
        cluster.send("effect.authorize", {"effect": identity})
        approved = cluster.client().state().effects[0]
        plan = intent._plan(approved.operation, approved.authorization)
        calls = []

        def provider_request(method, suffix, **kwargs):
            calls.append((method, suffix))
            if method == "POST":
                if suffix == "/git/trees":
                    return {"sha": intent.git_tree}
                if suffix == "/git/commits":
                    assert (
                        kwargs["payload"]
                        == staging_commit(contract, apply_patch(base, patch, contract))[1]
                    )
                    return {"sha": intent.head_commit}
                if suffix == "/git/refs":
                    assert kwargs["payload"] == {
                        "ref": "refs/heads/" + plan.branch,
                        "sha": intent.head_commit,
                    }
                    return {
                        "ref": "refs/heads/" + plan.branch,
                        "object": {"type": "commit", "sha": intent.head_commit},
                    }
                assert suffix == "/pulls" and kwargs["payload"]["draft"] is True
                assert kwargs["payload"]["body"] == plan.body
                return pull(plan)
            if suffix == "":
                return {"id": 42, "full_name": intent.repository, "archived": False}
            if suffix == "/actions/permissions":
                return {"enabled": False}
            if suffix.startswith("/git/ref/heads/"):
                return {
                    "object": {
                        "type": "commit",
                        "sha": intent.base_commit
                        if suffix.endswith("/main")
                        else intent.head_commit,
                    }
                }
            if suffix == "/git/commits/" + intent.base_commit:
                return {"sha": intent.base_commit, "tree": {"sha": tree_id(base)}}
            if suffix == "/git/commits/" + intent.head_commit:
                return {
                    "sha": intent.head_commit,
                    "tree": {"sha": intent.git_tree},
                    "parents": [{"sha": intent.base_commit}],
                }
            assert suffix == "/pulls"
            return [pull(plan)] if ("POST", "/pulls") in calls else []

        provider = Drafts(
            intent.repository,
            42,
            "owner",
            Token("fixture"),
            tmp_path / "provider.sqlite",
            enabled=True,
        )
        monkeypatch.setattr(provider, "_request", provider_request)
        client = cluster.client()
        sent = []

        def submit(raw):
            sent.append(raw)
            result = client.submit(raw)
            if document(raw)["command"]["kind"] == "effect.report":
                raise OSError("fixture drops already committed report reply")
            return result

        coordinator = Coordinator(
            tmp_path / "commands",
            client.live_state,
            submit,
            cluster.keys[("e0", 1)],
            chain=cluster.initial.chain,
            mission="repository",
            actor="e0",
            revision=1,
        )
        watchdog = Watchdog(
            client.live_state,
            chain=cluster.initial.chain,
            mission="repository",
            max_read_age_ns=30_000_000_000,
            max_stall_ns=30_000_000_000,
        )
        first = watchdog.poll()
        cluster.wait_height(first.height + 1)
        watchdog.poll()
        dispatcher = Dispatcher(
            provider,
            watchdog,
            Policy(path),
            objects,
            access,
            executor="e0",
            revision=1,
            staging=True,
        )
        inputs = Inputs(base_ref, patch_ref, inventory_ref, tuple(refs))
        from checkedflow.telemetry import Recorder

        recorder = Recorder()
        supervisor = Supervisor(tmp_path / "executor", coordinator, dispatcher, recorder=recorder)
        with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
            supervisor.step(identity, intent, contract, inputs)
        original = coordinator.pending()
        assert original is not None and document(original)["command"]["kind"] == "effect.report"
        supervisor = Supervisor(tmp_path / "executor", coordinator, dispatcher, recorder=recorder)
        assert supervisor.step(identity, intent, contract, inputs) == "observed"
        assert len(sent) == 2 and sent.count(original) == 1
        observations = recorder.drain()
        assert [row.outcome for row in observations] == ["failed", "returned"]
        assert observations[0].reason == "OUTCOME_UNKNOWN"
        assert all(row.operation == "effect.step" for row in observations)
        assert sum(method == "POST" for method, _ in calls) == 4
        raw = supervisor.observation(identity)
        assert raw is not None
        row = document(raw)
        assert row["outcome"] == "observed" and row["number"] == 7
        report_ref = Reference(
            "sha256",
            digest_bytes(raw),
            len(raw),
            "application/json",
            "evidence",
            "repository",
            intent.digest,
        )
        assert objects.get(report_ref, access=access) == raw
        cluster.wait_height(client.state().height)
        for index in range(4):
            state = cluster.client(index).state()
            assert (
                state.effects[0].status == "observed"
                and state.effects[0].evidence == report_ref.digest
            )
            assert state.budget.spent == 50 and state.budget.reserved == 0
        assert len(cluster.common_hash()[1]) == 64

        from checkedflow.effect_reconciliation import Reconciler
        from checkedflow.identity import public_key
        from checkedflow.observability import Monitor

        def storage_ready():
            return objects.get(base_ref, access=access) == tree_bytes(base)

        monitor = Monitor(
            watchdog,
            "effect_executor",
            {
                "configuration": lambda: bool(
                    Policy(path).authorize(client.live_state(), intent, provider, "e0", 1)
                ),
                "storage": storage_ready,
                "signer": lambda: any(
                    credential.identity == "e0"
                    and credential.revision == 1
                    and credential.public_key == public_key(cluster.keys[("e0", 1)])
                    for credential in client.live_state().credentials
                ),
                "provider": lambda: provider.enabled is True,
            },
        )
        assert monitor.observe().ready
        provider.enabled = False
        cluster.send("mission.pause", {})
        paused = monitor.observe()
        assert paused.readable and not paused.ready
        assert "checkedflow_protected_work_ready 0\n" in paused.prometheus()
        proposal = Reconciler(coordinator, provider, objects, access, Policy(path)).collect(
            identity, intent, contract, base_ref, patch_ref
        )
        assert objects.get(proposal.evidence, access=access) == proposal.observation
        command = document(proposal.command)
        reconciled = cluster.send(command["kind"], command["payload"])
        cluster.wait_height(int(reconciled["receipt"]["height"]))
        for index in range(4):
            state = cluster.client(index).state()
            assert state.effects[0].status == "reconciled" and state.effects[0].number == 7
            assert state.effects[0].evidence == proposal.evidence.digest
            assert state.budget.spent == 50 and state.budget.reserved == 0
        assert len(sent) == 2 and sum(method == "POST" for method, _ in calls) == 4
        assert len(cluster.common_hash()[1]) == 64
    finally:
        cluster.close()
