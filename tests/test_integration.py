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
                absent = engine.inspect(container) is None
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
        from checkedflow.worker_submission import Coordinator
        from checkedflow.worker_supervisor import Supervisor

        own = cluster.client(owner)
        access = Access(worker, frozenset({"repository"}), frozenset({"read", "write"}))
        artifacts = LocalStore(tmp_path / "evidence.sqlite")
        executor = RepositoryExecutor(base, patch, contract, cases)
        executed = []

        def confirmed_submit(raw):
            reply = own.submit(raw)
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
        assert supervisor.step(task) == "finished"
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
        cluster.send("mission.pause", {})
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
