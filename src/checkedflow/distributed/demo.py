"""Actual synthesis, gVisor verification, committed registration and iterative reuse."""

from pathlib import Path

from checkedflow.core.machine import accounting
from checkedflow.core.model import State
from checkedflow.core.values import JSON, Object, obj, require
from checkedflow.distributed.cluster import Cluster
from checkedflow.runner import GVisorRunner
from checkedflow.synthesis import CONTRACT
from checkedflow.wire import dumps
from checkedflow.worker import Worker


def bootstrap(cluster: Cluster, image: str) -> None:
    from checkedflow.identity import public_key

    for i in range(4):
        cluster.send(
            "worker.register",
            {
                "id": f"w{i}",
                "key": public_key(cluster.keys[f"w{i}"]),
                "organization": f"org{i}",
                "roles": ["producer", "executor", "verifier"],
            },
        )
    cluster.send(
        "verifier.register",
        {
            "id": "arrays-v1",
            "contract": CONTRACT,
            "image": image,
            "argv": ["python", "-B", "-s", "/work/check.py"],
            "exhaustive": True,
        },
    )
    for identity in ("reuse", "scratch"):
        cluster.send(
            "mission.create",
            {
                "id": identity,
                "workers": [f"w{i}" for i in range(4)],
                "budget": 4096,
                "verification_reserve": 4,
                "verification_cost": 1,
                "expires": 100000,
                "max_rounds": 3,
                "max_candidates": 256,
                "max_depth": 3,
                "max_attempts": 2,
                "verifier": "arrays-v1",
                "receiver": "laboratory",
                "contract": CONTRACT,
                "image": image,
            },
        )


def run_scenario(cluster: Cluster, image: str) -> Object:
    GVisorRunner(image).check()
    bootstrap(cluster, image)
    workers = [
        Worker(cluster.client(i), f"w{i}", cluster.keys[f"w{i}"], cluster.initial.chain)
        for i in range(4)
    ]
    targets = [["double"], ["double", "increment"], ["double", "increment", "reverse"]]
    observations: list[JSON] = []
    for mission in ("reuse", "scratch"):
        previous: list[str] = []
        for round_number, target in enumerate(targets, start=1):
            task_id = f"{mission}-{round_number}"
            dependencies = previous if mission == "reuse" else []
            cluster.send(
                "task.create",
                {
                    "id": task_id,
                    "mission": mission,
                    "phase": "generate",
                    "spec": {"target": list(target)},
                    "dependencies": list(dependencies),
                    "cost": 256,
                    "ttl": 1000,
                    "effect": "isolated",
                },
                actor="w0",
            )
            require(workers[0].once()["outcome"] == "reported", "DEMO", "generation failed")
            state = cluster.client().state()
            candidate = next(cid for cid, cap in state.capabilities.items() if cap.task == task_id)
            for worker in workers:
                cluster.wait_height(cluster.client().state().height)
                outcome = worker.once()
                require(outcome.get("outcome") == "pass", "DEMO", f"verification failed: {outcome}")
            state = cluster.client().state()
            require(state.capabilities[candidate].status == "checked", "DEMO", "not accepted")
            evidence = obj(state.tasks[task_id].result["evidence"])
            observations.append(
                {
                    "mission": mission,
                    "round": round_number,
                    "artifact": candidate,
                    "search_candidates": evidence["candidates_tried"],
                    "source_digest": state.capabilities[candidate].source_digest,
                    "behavior": state.capabilities[candidate].behavior,
                    "dependencies": list(dependencies),
                }
            )
            previous = [candidate]
    final = _final_state(cluster)
    height, fingerprint = cluster.common_hash()
    reports = [accounting(final, mid) for mid in ("reuse", "scratch")]
    attempts = {
        mid: sum(
            int(str(obj(row)["search_candidates"]))
            for row in observations
            if obj(row)["mission"] == mid
        )
        for mid in ("reuse", "scratch")
    }
    comparison = (
        "improved"
        if attempts["reuse"] < attempts["scratch"]
        else ("worsened" if attempts["reuse"] > attempts["scratch"] else "equal")
    )
    return {
        "status": "passed",
        "execution": "Linux Docker with gVisor runsc",
        "scope": "local laboratory; four keys do not establish organizational independence",
        "common_committed_height": height,
        "common_app_hash": fingerprint,
        "missions": list(reports),
        "observations": observations,
        "comparison": {
            "metric": "bounded grammar candidates including initial formation",
            "reuse": attempts["reuse"],
            "scratch": attempts["scratch"],
            "outcome": comparison,
            "causal_generalization": None,
            "reason": "One declared finite task sequence; no population inference.",
        },
    }


def _final_state(cluster: Cluster) -> State:
    """Read after all completed worker writes have crossed the four-node barrier."""
    barrier = max(cluster.client(index).state().height for index in range(4)) + 2
    cluster.wait_height(barrier)
    final = cluster.client().state()
    require(final.height >= barrier, "DEMO", "accounting snapshot precedes final barrier")
    return final


def demonstrate(directory: Path, image: str, cometbft: str) -> Object:
    GVisorRunner(image).check()
    cluster = Cluster(directory, cometbft)
    try:
        cluster.start()
        report = run_scenario(cluster, image)
        (directory / "acceptance.json").write_bytes(dumps(report) + b"\n")
        return report
    finally:
        cluster.close()
