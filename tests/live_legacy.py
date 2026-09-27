"""Owned live legacy laboratory cutover; no production services or credentials."""

import pytest

from checkedflow.core.values import Failure
from checkedflow.distributed.cluster import Cluster
from checkedflow.distributed.demo import run_scenario
from checkedflow.recovery import replay_blocks
from checkedflow.serialization import encode
from checkedflow.storage import Store


def capture(directory, binary, image):
    cluster = Cluster(directory, binary, base_port=30650)
    processes = []
    try:
        cluster.start()
        processes = list(cluster.processes.values())
        run_scenario(cluster, image)
        cluster.send(
            "task.create",
            {
                "id": "unknown",
                "mission": "reuse",
                "phase": "execute",
                "spec": {},
                "dependencies": [],
                "cost": 1,
                "ttl": 10,
                "effect": "isolated",
            },
            actor="w0",
        )
        cluster.send("task.lease", {"id": "unknown"}, actor="w0")
        cluster.wait_height(cluster.client().state().tasks["unknown"].deadline)
        assert cluster.client().state().tasks["unknown"].status == "uncertain"
        root = next(
            identity
            for identity, cap in cluster.client().state().capabilities.items()
            if cap.mission == "reuse" and not cap.dependencies
        )
        cluster.send("capability.revoke", {"id": root, "reason": "migration regression"})
        for index in range(4):
            cluster.send("worker.revoke", {"id": f"w{index}", "reason": "migration shutdown"})
        with pytest.raises(Failure, match="REJECTED"):
            cluster.send("task.lease", {"id": "unknown"}, actor="w0")
        cluster.wait_height(cluster.client().state().height + 1)
        height, fingerprint = cluster.common_hash()
    finally:
        cluster.close()
    assert processes and all(process.poll() is not None for process in processes)
    # Stop first; then read durable histories without copying a live SQLite/WAL file.
    stores = [
        Store(directory / f"node{i}" / "application.sqlite3", cluster.initial) for i in range(4)
    ]
    journals = [
        [block for block in store.blocks() if block["height"] <= height] for store in stores
    ]
    recovered = [replay_blocks(cluster.initial, blocks) for blocks in journals]
    assert all(runtime.state_hash.upper() == fingerprint.upper() for runtime in recovered)
    assert all(encode(runtime.state) == encode(recovered[0].state) for runtime in recovered)
    state = recovered[0].state
    assert all(worker.revoked for worker in state.workers.values())
    assert state.tasks["unknown"].status == "uncertain"
    assert any(cap.status == "quarantined" for cap in state.capabilities.values())
    return {
        "initial": encode(cluster.initial),
        "blocks": journals[0],
        "final_state": encode(state),
        "final_state_hash": recovered[0].state_hash,
    }, cluster.keys
