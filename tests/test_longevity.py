"""Opt-in installed-artifact longevity measurement; not selected by ordinary qualification."""

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import signal
import sqlite3
import sysconfig
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
from importlib.resources import files
from io import BytesIO
from pathlib import Path, PurePosixPath

import pytest
from patch_fixture import invoice
from test_integration import infrastructure as sandbox_infrastructure

import checkedflow
from checkedflow.artifact_io import Access
from checkedflow.artifacts import LocalStore
from checkedflow.core.artifact import Reference
from checkedflow.core.values import Failure
from checkedflow.core.work_acceptance import status as acceptance_status
from checkedflow.distributed.operational_cluster import Cluster
from checkedflow.operational_codec import state_bytes
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import Store
from checkedflow.repository_execution import observe_patch
from checkedflow.repository_reuse import tree_bytes
from checkedflow.sandbox_recovery import LABEL, Docker
from checkedflow.wire import digest, document, dumps


@pytest.fixture
def infrastructure(runtime_infrastructure, tmp_path, monkeypatch):
    yield from sandbox_infrastructure.__wrapped__(runtime_infrastructure, tmp_path, monkeypatch)


def distribution_identity(wheel):
    """Compare every installed package resource with the supplied immutable wheel."""
    origin = Path(checkedflow.__file__).resolve().parent
    assert origin == (Path(sysconfig.get_path("purelib")) / "checkedflow").resolve()
    with zipfile.ZipFile(wheel) as archive:
        members = [
            name
            for name in archive.namelist()
            if name.startswith("checkedflow/") and not name.endswith("/")
        ]
        assert members
        assert len(set(members)) == len(members)
        for name in members:
            assert ".." not in PurePosixPath(name).parts
            assert (origin / name.removeprefix("checkedflow/")).resolve().is_relative_to(origin)
            assert (origin / name.removeprefix("checkedflow/")).read_bytes() == archive.read(name)
    return {
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "package_members": len(members),
    }


def quantiles(samples):
    ordered = sorted(samples)
    return (
        {str(p): ordered[max(0, math.ceil(len(ordered) * p / 100) - 1)] for p in (50, 95, 99)}
        if ordered
        else None
    )


def observe_during_drain(measurement, base, patch, contract, cases, height):
    """Observe an owned running container before committing drain; no synthetic observation."""
    journal = Path(os.environ["CHECKEDFLOW_SANDBOX_RECOVERY"]) / "sandbox.sqlite"
    engine = Docker()
    with closing(sqlite3.connect(journal)) as db:
        prior = {row[0] for row in db.execute("SELECT name FROM containers")}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(observe_patch, base, patch, contract, cases, height=height)
        deadline = time.monotonic() + 15
        running = False
        while time.monotonic() < deadline and not future.done():
            with closing(sqlite3.connect(journal)) as db:
                rows = db.execute("SELECT name,token FROM containers").fetchall()
            for name, token in rows:
                if name not in prior:
                    observed = engine.inspect(name)
                    if observed and observed["State"]["Running"]:
                        assert observed["Config"]["Labels"][LABEL] == token
                        running = True
                        break
            if running:
                break
            time.sleep(0.05)
        assert running, "could not witness the owned sandbox before drain"
        started = time.monotonic_ns()
        measurement.send("mission.drain", {})
        assert measurement.cluster.client().state().mode == "draining"
        observed = engine.inspect(name)
        assert observed and observed["State"]["Running"], "sandbox ended before drain committed"
        assert observed["Config"]["Labels"][LABEL] == token
        with pytest.raises(Failure, match="REJECTED"):
            measurement.cluster.send(
                "budget.reserve",
                {
                    "phase": "verify",
                    "ceiling": 1,
                    "target": digest(document(json.dumps(asdict(contract)).encode())),
                },
            )
        return future.result(timeout=15), started


def retired_request(cluster, raw):
    """Distinguish an ambiguous duplicate RPC reply from actual application rejection."""
    import grpc

    from checkedflow.distributed.proto.generated.tendermint.abci import types_pb2 as pb
    from checkedflow.distributed.proto.generated.tendermint.abci import types_pb2_grpc as rpc

    before = cluster.client().state()
    for index in range(4):
        with grpc.insecure_channel(f"127.0.0.1:{cluster.processes.port(index, 2)}") as channel:
            response = rpc.ABCIStub(channel).CheckTx(pb.RequestCheckTx(tx=raw), timeout=10)
        assert response.code == 1 and response.codespace == "RETIRED_REQUEST"
    # CometBFT can reject identical bytes in its transaction cache before calling ABCI.
    # Such a transport reply remains unknown to the SDK; do not reclassify it as a receipt.
    with pytest.raises(Failure) as caught:
        cluster.client().submit(raw)
    assert caught.value.code in {"REJECTED", "OUTCOME_UNKNOWN"}
    cluster.wait_height(before.height + 1)
    for index in range(4):
        after = cluster.client(index).state()
        assert replace(after, height=before.height) == before
    return {
        "application_rejections": 4,
        "codespace": "RETIRED_REQUEST",
        "rpc_outcome": caught.value.code,
        "business_state_unchanged": True,
    }


def verify_stored_history(store, checkpoint):
    """Require the independent observed checkpoint within the replayed signed history."""
    state = store.load()
    anchored = []

    def check_anchor(raw):
        block = document(raw)
        if block["height"] == checkpoint[0]:
            assert block["state_hash"].upper() == checkpoint[1].upper()
            anchored.append(True)

    assert (
        store.verify_history(expected_hash=Runtime(state).state_hash, consume=check_anchor) == state
    )
    assert anchored == [True], "committed checkpoint missing from stored history"
    return state


class Measurement:
    def __init__(self, cluster, root, plan):
        self.cluster, self.root, self.plan = cluster, root, plan
        self.started = time.monotonic_ns()
        self.commands = []
        self.resources = []
        self.accepted = []
        self.arrivals = []
        self.archive_roots = []
        self.disk_baseline = self.disk_bytes()

    def disk_bytes(self):
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def sample(self):
        rss = 0
        processes = self.cluster.processes.processes.values()
        for pid in (os.getpid(), *(process.pid for process in processes if process.poll() is None)):
            try:
                lines = Path(f"/proc/{pid}/status").read_text().splitlines()
            except FileNotFoundError:
                continue
            rss += sum(int(line.split()[1]) * 1024 for line in lines if line.startswith("VmRSS:"))
        state = self.cluster.client().state()
        disk = self.disk_bytes()
        self.resources.append(
            {
                "elapsed_ns": time.monotonic_ns() - self.started,
                "rss_bytes": rss,
                "disk_bytes": disk,
                "height": state.height,
                "state_bytes": len(state_bytes(state)),
                "receipts": len(state.journal.receipts),
                "candidates": len(state.candidates),
                "tasks": len(state.tasks),
            }
        )
        assert rss <= self.plan["max_sampled_rss_bytes"]
        assert disk <= self.plan["max_disk_bytes"]
        assert len(state_bytes(state)) <= self.plan["max_state_bytes"]
        assert len(state.journal.receipts) <= self.plan["max_receipts"]

    def raw(self, kind, payload, *, actor="org0", node=0):
        before = time.monotonic_ns()
        result = self.cluster.send(kind, payload, actor=actor, node=node)
        self.commands.append({"kind": kind, "latency_ns": time.monotonic_ns() - before})
        return result

    def send(self, kind, payload, *, actor="org0", node=0):
        state = self.cluster.client().state()
        if (
            sum(receipt.administrative for receipt in state.journal.receipts) >= 8
            or len(state.journal.receipts) >= 96
        ):
            self.raw("mission.pause", {})
            self.raw("journal.rollover", {})
            if state.mode == "running":
                self.raw("mission.resume", {})
            elif state.mode == "draining":
                self.raw("mission.drain", {})
        return self.raw(kind, payload, actor=actor, node=node)

    def arrival(self, phase, index, phase_start, interval_ms, total):
        scheduled = phase_start + index * interval_ms * 1000000
        while (remaining := scheduled - time.monotonic_ns()) > 0:
            time.sleep(min(remaining / 1000000000, 1))
        now = time.monotonic_ns()
        due = min(total, (now - phase_start) // (interval_ms * 1000000) + 1)
        self.arrivals.append(
            {
                "phase": phase,
                "index": index,
                "scheduled_ns": scheduled - self.started,
                "wait_ns": max(0, now - scheduled),
                "queued_intents": max(0, due - index),
            }
        )


@pytest.mark.integration
@pytest.mark.sandbox
@pytest.mark.longevity
def test_installed_repository_longevity(infrastructure, tmp_path):
    plan = document(files("checkedflow").joinpath("data/load-profile.json").read_bytes())
    run_workload(
        plan,
        infrastructure,
        tmp_path,
        Path(os.environ["CHECKEDFLOW_LOAD_WHEEL"]),
        Path(os.environ["CHECKEDFLOW_LOAD_REPORT"]),
    )


@pytest.mark.integration
@pytest.mark.sandbox
@pytest.mark.qualification
def test_installed_longevity_path_smoke(infrastructure, tmp_path):
    """A separately named short path check; never a measurement of the frozen full workload."""
    plan = document(files("checkedflow").joinpath("data/load-profile.json").read_bytes())
    plan.update(
        name="repository-longevity-smoke-2",
        control_requests=17,
        candidates=2,
        control_interval_ms=1,
        candidate_interval_ms=1,
        max_seconds=300,
        drain_candidate=1,
    )
    wheels = tuple(Path("dist").glob("*.whl"))
    assert len(wheels) == 1
    run_workload(plan, infrastructure, tmp_path, wheels[0], Path("reports/longevity-smoke"))
    result = document(Path("reports/longevity-smoke/result.json").read_bytes())
    assert result["status"] == "COMPONENT_MEASURED"
    assert result["control_completed"] == 17 and result["verified_completions"] == 2
    assert result["cleanup_failure"] is None
    assert result["in_flight_drain_ns"] > 0


def run_workload(plan, infrastructure, tmp_path, wheel, report):
    """One fixed schedule; failures preserve the original manifest and partial observations."""
    assert platform.system() == "Linux", "qualified workload requires Linux /proc measurements"
    assert not report.exists(), "never overwrite another workload run"
    image, binary = infrastructure
    executable = shutil.which(binary)
    assert executable is not None
    manifest = {
        "profile": plan,
        "profile_sha256": digest(plan),
        "wheel": distribution_identity(wheel),
        "cpu_count": os.cpu_count(),
        "kernel": platform.release(),
        "memory_total_kib": int(
            next(
                line.split()[1]
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemTotal:")
            )
        ),
        "image": image,
        "cometbft_sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
        "package_version": importlib.metadata.version("checkedflow"),
        "status": "RUNNING",
        "not_release_authority": True,
    }
    assert isinstance(manifest["cpu_count"], int) and manifest["cpu_count"] > 0
    report.mkdir(parents=True)
    (report / "manifest.json").write_bytes(dumps(manifest))
    cluster = Cluster(tmp_path / "cluster", binary)
    measurement = Measurement(cluster, tmp_path, plan)
    artifacts = LocalStore(tmp_path / "artifacts.sqlite")
    access = Access("load-observer", frozenset({"repository"}), frozenset({"read", "write"}))
    success, failure = False, None
    recovery_ns = drain_ns = in_flight_drain_ns = None
    old_request_result = None
    checkpoint = None
    control_completed = control_elapsed_ns = verified_elapsed_ns = 0

    def deadline(signum, frame):
        raise TimeoutError("frozen workload deadline exceeded")

    previous = signal.signal(signal.SIGALRM, deadline)
    signal.alarm(plan["max_seconds"])
    try:
        cluster.start()
        measurement.send(
            "budget.configure",
            {
                "budget": plan["modeled_budget"],
                "verification_reserve": plan["verification_reserve"],
            },
        )
        measurement.send("mission.resume", {})
        control_start = time.monotonic_ns()
        for index in range(plan["control_requests"]):
            measurement.arrival(
                "control",
                index,
                control_start,
                plan["control_interval_ms"],
                plan["control_requests"],
            )
            measurement.send("mission.resume", {})
            control_completed += 1
            control_elapsed_ns = time.monotonic_ns() - control_start
            if index % 32 == 0:
                measurement.sample()
        # Recover one real process/disk identity without reinitializing its signing state.
        began = time.monotonic_ns()
        cluster.stop_node(3, crash=True)
        cluster.wait_height(cluster.client().state().height + 2, nodes=(0, 1, 2))
        cluster.start_node(3)
        cluster.wait_height(cluster.client().state().height + 2)
        recovery_ns = time.monotonic_ns() - began
        phase_start = time.monotonic_ns()
        fixture = document(files("checkedflow").joinpath("data/invoice-fixture.json").read_bytes())
        for index in range(plan["candidates"]):
            measurement.arrival(
                "verified", index, phase_start, plan["candidate_interval_ms"], plan["candidates"]
            )
            began = time.monotonic_ns()
            content = fixture["replacement"]["content"] + f"\n# load variant {index}\n"
            if index == plan["drain_candidate"]:
                delay = plan["drain_delay_seconds"]
                content += f"\nimport time as _load_time\n_load_time.sleep({delay})\n"
            base, patch, contract, cases = invoice(image, content)
            contract = replace(contract, deadline_height=1000000)
            if index == plan["drain_candidate"]:
                contract = replace(contract, cpu_seconds=plan["drain_cpu_seconds"])
            target = digest(document(json.dumps(asdict(contract)).encode()))

            def publish(kind, body, manifest=target):
                ref = Reference(
                    "sha256",
                    hashlib.sha256(body).hexdigest(),
                    len(body),
                    "application/json",
                    kind,
                    "repository",
                    manifest,
                )
                artifacts.put(ref, BytesIO(body), access=access)
                assert artifacts.get(ref, access=access) == body
                return ref.digest

            for kind, body in (
                ("source-tree", tree_bytes(base)),
                ("patch", patch),
                ("evidence", cases),
            ):
                publish(kind, body)
            checks, tickets = [], []
            for verifier in range(plan["funded_checks_per_candidate"]):
                ticket = measurement.send(
                    "budget.reserve",
                    {"phase": "verify", "ceiling": plan["verification_cost"], "target": target},
                )["request"]
                tickets.append(ticket)
                checks.append(
                    measurement.send(
                        "task.admit",
                        {
                            "ticket": ticket,
                            "workers": [f"v{verifier}"],
                            "lease_blocks": 1000,
                            "expires": 1000000,
                            "max_attempts": 1,
                        },
                    )["request"]
                )
            candidate = measurement.send(
                "artifact.admit",
                {
                    "target": target,
                    "artifact": contract.result_tree,
                    "expires": 1000000,
                    "checks": checks,
                },
            )["request"]
            for verifier, task in enumerate(checks[: plan["verifiers_per_candidate"]]):
                actor = f"v{verifier}"
                measurement.send("task.lease", {"task": task}, actor=actor, node=verifier)
                measurement.send(
                    "task.start", {"task": task, "fence": 1}, actor=actor, node=verifier
                )
                injected = index == plan["drain_candidate"] and verifier == 2
                if injected:
                    observation, drain_started = observe_during_drain(
                        measurement,
                        base,
                        patch,
                        contract,
                        cases,
                        cluster.client(verifier).state().height,
                    )
                else:
                    observation = observe_patch(
                        base, patch, contract, cases, height=cluster.client(verifier).state().height
                    )
                assert observation.case_match is True and observation.contract_digest == target
                evidence = publish(
                    "evidence", dumps(document(json.dumps(asdict(observation)).encode()))
                )
                measurement.send(
                    "task.finish",
                    {"task": task, "fence": 1, "outcome": "reported", "evidence": evidence},
                    actor=actor,
                    node=verifier,
                )
                if injected:
                    in_flight_drain_ns = time.monotonic_ns() - drain_started
                    observed = cluster.client().state()
                    assert observed.mode == "draining"
                    assert (
                        next(item for item in observed.tasks if item.identity == task).status
                        == "finished"
                    )
                    measurement.send("mission.resume", {})
                measurement.send(
                    "artifact.attest",
                    {"candidate": candidate, "task": task, "evidence": evidence, "verdict": "pass"},
                    actor=actor,
                    node=verifier,
                )
            state = cluster.client().state()
            selected = next(item for item in state.candidates if item.identity == candidate)
            assert acceptance_status(selected, state.credentials, state.height) == "accepted"
            measurement.accepted.append(
                {"latency_ns": time.monotonic_ns() - began, "artifact": contract.result_tree}
            )
            verified_elapsed_ns = time.monotonic_ns() - phase_start
            for task in checks[plan["verifiers_per_candidate"] :]:
                measurement.send("task.cancel", {"task": task})
            measurement.sample()
            measurement.send("artifact.revoke", {"candidate": candidate})
            measurement.raw("mission.pause", {})
            measurement.raw("journal.rollover", {})
            state = cluster.client().state()
            measurement.raw(
                "history.archive",
                {
                    "expected_root": state.history.root,
                    "tickets": tickets,
                    "tasks": checks,
                    "candidates": [candidate],
                },
            )
            measurement.archive_roots.append(cluster.client().state().history.root)
            measurement.raw("mission.resume", {})
        began = time.monotonic_ns()
        measurement.send("mission.drain", {})
        state = cluster.client().state()
        assert state.mode == "draining" and not state.tasks and state.budget.reserved == 0
        measurement.send("mission.pause", {})
        drain_ns = time.monotonic_ns() - began
        cluster.wait_height(cluster.client().state().height + 1)
        cluster.common_hash()
        assert (
            state.budget.spent
            == plan["candidates"] * plan["verifiers_per_candidate"] * plan["verification_cost"]
        )
        assert len({row["artifact"] for row in measurement.accepted}) == plan["candidates"]
        # Original epoch commands cannot regain authority after repeated rollovers.
        with closing(sqlite3.connect(cluster.directory / "node0" / "operational.sqlite")) as db:
            first = next(
                tx["raw"]
                for (body,) in db.execute("SELECT body FROM blocks ORDER BY height")
                for tx in document(body)["transactions"]
            )
        old_request_result = retired_request(cluster, bytes.fromhex(first))
        checkpoint = cluster.common_hash()
        success = True
    except BaseException as error:
        failure = type(error).__name__
        raise
    finally:
        cleanup_error = None
        signal.alarm(plan["cleanup_seconds"])
        try:
            cluster.close()
            if success:
                assert checkpoint is not None
                for index in range(4):
                    store = Store(
                        cluster.directory / f"node{index}" / "operational.sqlite", cluster.initial
                    )
                    verify_stored_history(store, checkpoint)
                    for sequence, root in enumerate(measurement.archive_roots, 1):
                        store.work_archive(sequence, expected_root=root)
        except BaseException as error:
            success = False
            cleanup_error = error
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        completed_tasks = sum(row["kind"] == "task.finish" for row in measurement.commands)
        disk_growth = measurement.disk_bytes() - measurement.disk_baseline
        summary = {
            "status": "COMPONENT_MEASURED" if success else "FAILED",
            "failure": failure,
            "not_release_authority": True,
            "elapsed_ns": time.monotonic_ns() - measurement.started,
            "command_latency_ns": quantiles([row["latency_ns"] for row in measurement.commands]),
            "verified_latency_ns": quantiles([row["latency_ns"] for row in measurement.accepted]),
            "verified_completions": len(measurement.accepted),
            "planned_control_intents": plan["control_requests"],
            "control_completed": control_completed,
            "control_elapsed_ns": control_elapsed_ns,
            "verified_elapsed_ns": verified_elapsed_ns,
            "cleanup_failure": type(cleanup_error).__name__ if cleanup_error else None,
            "recovery_ns": recovery_ns,
            "empty_drain_ns": drain_ns,
            "in_flight_drain_ns": in_flight_drain_ns,
            "retired_request": old_request_result,
            "common_checkpoint": (
                {"height": checkpoint[0], "app_hash": checkpoint[1]} if checkpoint else None
            ),
            "queue_peak": max((row["queued_intents"] for row in measurement.arrivals), default=0),
            "disk_growth_bytes": disk_growth,
            "completed_tasks": completed_tasks,
            "disk_bytes_per_completed_task": (
                {"numerator": disk_growth, "denominator": completed_tasks}
                if completed_tasks
                else None
            ),
            "novelty_claim": plan["novelty_claim"],
            "resource_sampling": (
                "every32controlintents; everyverifiedcandidate; "
                "sampledRSS excludes short-lived sandbox processes"
            ),
            "remaining": ["full operational G5/G6/G7 qualification"],
        }
        (report / "result.json").write_bytes(dumps(summary))
        for name in ("commands", "resources", "accepted", "arrivals", "archive_roots"):
            (report / (name + ".json")).write_text(
                json.dumps(getattr(measurement, name)), encoding="utf-8"
            )
        if cleanup_error is not None:
            raise cleanup_error
