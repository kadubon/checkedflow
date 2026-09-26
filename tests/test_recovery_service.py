"""Real, finite systemd restart qualification on an explicitly disposable Linux host."""

import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from checkedflow.core.values import Failure
from checkedflow.sandbox_recovery import Docker, Recovery, boot_id


def call(*args):
    return subprocess.run(
        args, capture_output=True, text=True, check=True, timeout=15
    ).stdout.strip()


def wait_for(predicate, message, *, seconds=30):
    deadline = time.monotonic() + seconds
    while True:
        value = predicate()
        if value:
            return value
        assert time.monotonic() < deadline, message
        time.sleep(0.1)


@pytest.mark.sandbox
@pytest.mark.qualification
def test_systemd_restarts_recovery_after_worker_and_service_sigkill(
    runtime_infrastructure, tmp_path, monkeypatch
):
    image, _ = runtime_infrastructure
    engine = Docker()
    directory = tmp_path / "systemd-recovery"
    recovery = Recovery(directory, engine, boot_id())
    monkeypatch.setenv("CHECKEDFLOW_SANDBOX_RECOVERY", str(directory))
    unit = "checkedflow-recovery-qualification-" + uuid.uuid4().hex + ".service"
    worker = None

    def show(name):
        return call("sudo", "-n", "systemctl", "show", "--value", "--property=" + name, unit)

    def ready():
        try:
            recovery.ready()
        except Failure:
            return False
        return True

    script = (
        "import sys\n"
        "from checkedflow.runner import GVisorRunner, Limits\n"
        "GVisorRunner(sys.argv[1], limits=Limits(seconds=15)).run(\n"
        "    ('python', '-c', 'while True: pass'), {}, None)\n"
    )
    systemd_version = call("systemd-run", "--version").splitlines()[0]
    try:
        call(
            "sudo",
            "-n",
            "systemd-run",
            "--quiet",
            "--collect",
            "--unit=" + unit,
            "--property=Type=exec",
            "--property=Restart=on-failure",
            "--property=RestartSec=1",
            "--property=RuntimeMaxSec=45",
            "--property=StartLimitIntervalSec=180",
            "--property=StartLimitBurst=3",
            "--property=UMask=0077",
            "--property=User=" + str(os.getuid()),
            "--property=Group=" + str(os.getgid()),
            sys.executable,
            "-I",
            "-m",
            "checkedflow.sandbox_recovery",
            str(directory),
        )
        wait_for(ready, "systemd recovery never became ready")
        original_pid = int(show("MainPID"))
        assert original_pid > 1
        started = time.monotonic()
        with (tmp_path / "worker.log").open("wb") as log:
            worker = subprocess.Popen(
                [sys.executable, "-I", "-c", script, image], stdout=log, stderr=log
            )

            def running():
                with sqlite3.connect(recovery.path) as db:
                    row = db.execute("SELECT container FROM containers").fetchone()
                if row is None or not row[0]:
                    return False
                info = engine.inspect(row[0])
                return info["Id"] if info is not None and info["State"]["Running"] else False

            container = wait_for(running, "real gVisor container did not start", seconds=12)
            worker.kill()
            worker.wait(timeout=10)
            assert worker.returncode == -9
            call("sudo", "-n", "systemctl", "kill", "--kill-whom=main", "--signal=KILL", unit)

            def restarted():
                current = int(show("MainPID"))
                return current > 1 and current != original_pid and ready()

            wait_for(restarted, "systemd did not restart the recovery process", seconds=12)
            restarts = int(show("NRestarts"))
            assert restarts >= 1

            def removed():
                absent = engine.inspect(container) is None
                with sqlite3.connect(recovery.path) as db:
                    empty = db.execute("SELECT COUNT(*) FROM containers").fetchone()[0] == 0
                return absent and empty

            wait_for(removed, "restarted recovery did not retire the orphan", seconds=25)
            report = {
                "status": "passed",
                "systemd": systemd_version,
                "worker_signal": "SIGKILL",
                "recovery_signal": "SIGKILL",
                "recovery_restarts": restarts,
                "container_deadline_seconds": 15,
                "observed_total_seconds": round(time.monotonic() - started, 3),
                "automatic_candidate_reexecution": False,
                "scope": "one disposable host; no journal loss or Docker outage injected",
            }
    finally:
        if worker is not None:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=10)
        try:
            recovery.sweep()
            with sqlite3.connect(recovery.path) as db:
                pending = db.execute("SELECT name FROM containers").fetchall()
            for (name,) in pending:
                assert recovery.cleanup(name), "unresolved laboratory creation"
        finally:
            call("sudo", "-n", "systemctl", "stop", unit)
    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    (reports / "sandbox-service.json").write_text(json.dumps(report, indent=2) + "\n")
