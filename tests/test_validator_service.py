"""Local custody-template behavior, not cross-host ownership or CometBFT qualification."""

import configparser
import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
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


@pytest.mark.integration
@pytest.mark.qualification
def test_systemd_comet_custody_stop_and_conflict(tmp_path):
    if sys.platform != "linux":
        pytest.skip("Disposable Linux systemd qualification required")
    binary = os.environ.get("CHECKEDFLOW_COMETBFT")
    if not binary:
        pytest.skip("Pinned real CometBFT required")
    from test_recovery_service import call, wait_for

    from checkedflow.distributed.operational_cluster import Cluster

    # Copy the executable into the disposable fixture: ProtectHome hides checkout paths.
    executable = tmp_path / "cometbft"
    shutil.copy2(binary, executable)
    cluster = Cluster(tmp_path / "service", str(executable), base_port=29850)
    home = cluster.directory / "node0"
    name = "checkedflow-validator-test-" + uuid.uuid4().hex + ".service"
    properties = dict(unit()["Service"])
    properties.update(
        user=str(os.getuid()),
        group=str(os.getgid()),
        workingdirectory=str(home),
        readwritepaths=str(home),
    )
    command = shlex.split(properties.pop("execstart"))[:6]
    command += [str(home / "custody.lock"), str(executable), "start", "--home", str(home)]
    # systemd's D-Bus property names are case sensitive; keep the template's spelling.
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(files("checkedflow").joinpath("data/validator.service").read_text())
    options = [
        "--property=" + key + "=" + properties[key.lower()]
        for key in parser["Service"]
        if key != "ExecStart"
    ]
    started = False

    def show(property_name):
        return call(
            "sudo", "-n", "systemctl", "show", "--value", "--property=" + property_name, name
        )

    try:
        cluster.start()
        cluster.processes.stop_process("comet0")
        original = (home / "config/priv_validator_key.json").read_bytes()
        prior_height = int(
            json.loads((home / "data/priv_validator_state.json").read_bytes())["height"]
        )
        call(
            "sudo",
            "-n",
            "systemd-run",
            "--quiet",
            "--unit=" + name,
            "--property=RuntimeMaxSec=90",
            "--property=StartLimitIntervalSec=180",
            "--property=StartLimitBurst=1",
            "--property=BindPaths=" + str(home),
            "--property=BindReadOnlyPaths=" + str(executable),
            *options,
            *command,
        )
        started = True
        wait_for(lambda: show("ActiveState") == "active", "validator service not active")
        cluster.wait_height(cluster.client(1).state().height + 2)
        assert subprocess.run(command, capture_output=True, timeout=10).returncode == 73
        assert show("NRestarts") == "0"
        call("sudo", "-n", "systemctl", "stop", name)
        assert show("ActiveState") == "inactive" and show("MainPID") == "0"
        # A successful service stop must relinquish the lock, without replacing its inode.
        assert subprocess.run(command[:7] + ["/usr/bin/true"], timeout=10).returncode == 0
        assert (home / "config/priv_validator_key.json").read_bytes() == original
        assert (
            int(json.loads((home / "data/priv_validator_state.json").read_bytes())["height"])
            >= prior_height
        )
        cluster.wait_height(cluster.client(1).state().height + 2, nodes=(1, 2, 3))
    finally:
        try:
            if started:
                call("sudo", "-n", "systemctl", "stop", name)
        finally:
            cluster.close()
