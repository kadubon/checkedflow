"""Local custody-template behavior, not cross-host ownership or CometBFT qualification."""

import configparser
import shlex
import subprocess
import sys
from importlib.resources import files

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
