"""Local custody-template behavior, not cross-host ownership or CometBFT qualification."""

import configparser
import json
import os
import shlex
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest


def unit():
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(files("checkedflow").joinpath("data/validator.service").read_text())
    return parser


def test_validator_template_retains_child_lock_and_refuses_conflicts():
    service = unit()["Service"]
    command = shlex.split(service["ExecStart"])
    assert command[:7] == [
        "/usr/bin/flock",
        "--exclusive",
        "--nonblock",
        "--conflict-exit-code",
        "73",
        "--no-fork",
        "/var/lib/checkedflow/validator/custody.lock",
    ]
    assert service["RestartPreventExitStatus"] == "73"
    assert service["KillMode"] == "control-group"
    assert service["SendSIGKILL"] == "no"
    assert service["User"] != "root"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux flock descriptor contract")
def test_exec_child_holds_lock_until_process_exits(tmp_path):
    command = shlex.split(unit()["Service"]["ExecStart"])[:6]
    lock = str(tmp_path / "custody.lock")
    child = [
        sys.executable,
        "-u",
        "-c",
        'import sys; print("ready", flush=True); sys.stdin.buffer.read(1)',
    ]
    process = subprocess.Popen(
        command + [lock, *child],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        import select

        assert select.select([process.stdout], [], [], 10)[0], "lock holder did not become ready"
        assert process.stdout.readline() == b"ready\n"
        contender = subprocess.run(
            command + [lock, sys.executable, "-c", "raise SystemExit(99)"], timeout=10
        )
        assert contender.returncode == 73
        process.communicate(input=b"x", timeout=10)
        assert process.returncode == 0
        successor = subprocess.run(
            command + [lock, sys.executable, "-c", "raise SystemExit(0)"], timeout=10
        )
        assert successor.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


@pytest.mark.integration
@pytest.mark.qualification
def test_actual_comet_validator_lock_and_signing_state_restart(tmp_path):
    if sys.platform != "linux":
        pytest.skip("Linux validator custody contract")
    binary = os.environ.get("CHECKEDFLOW_COMETBFT")
    if not binary:
        pytest.skip("Pinned real CometBFT required")
    from checkedflow.distributed.operational_cluster import Cluster

    cluster = Cluster(tmp_path / "custody", binary, base_port=29750)
    home = cluster.directory / "node0"
    key = home / "config/priv_validator_key.json"
    state = home / "data/priv_validator_state.json"
    original_key = key.read_bytes()
    prefix = shlex.split(unit()["Service"]["ExecStart"])[:6]
    command = [
        *prefix,
        str(home / "custody.lock"),
        str(Path(binary).resolve()),
        "start",
        "--home",
        str(home),
    ]
    try:
        cluster.start()
        cluster.processes.stop_process("comet0")
        old_height = int(json.loads(state.read_bytes())["height"])
        cluster.processes._spawn("comet0", command)
        cluster.wait_height(cluster.client(1).state().height + 3)
        competitor = subprocess.run(command, capture_output=True, timeout=10)
        assert competitor.returncode == 73, competitor.stderr
        cluster.processes.stop_process("comet0")
        signed_height = int(json.loads(state.read_bytes())["height"])
        assert signed_height >= old_height
        # The other three validators must keep committing while this node is stopped.
        cluster.wait_height(cluster.client(1).state().height + 2, nodes=(1, 2, 3))
        cluster.processes._spawn("comet0", command)
        cluster.wait_height(cluster.client(1).state().height + 2)
        assert cluster.common_hash()[1]
        cluster.processes.stop_process("comet0")
        assert int(json.loads(state.read_bytes())["height"]) >= signed_height
        assert key.read_bytes() == original_key
    finally:
        cluster.close()
