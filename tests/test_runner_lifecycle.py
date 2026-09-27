"""Host-side launch sequencing with inert process doubles; live isolation is separate."""

import io
import sqlite3
import subprocess

import pytest
from test_sandbox_recovery import BOOT, CID, Engine

from checkedflow.core.values import Failure
from checkedflow.runner import GVisorRunner
from checkedflow.sandbox_recovery import LABEL, Recovery


@pytest.mark.parametrize(
    "failure", ["none", "creation", "timeout", "encoding", "launch", "cleanup", "deadline"]
)
def test_execution_starts_only_an_owned_durably_bound_id(tmp_path, monkeypatch, failure):
    import checkedflow.runner as adapter

    engine = Engine()
    recovery = Recovery(tmp_path, engine, BOOT)
    recovery.sweep(advertise=True)
    monkeypatch.setenv("CHECKEDFLOW_SANDBOX_RECOVERY", str(tmp_path))
    monkeypatch.setattr(adapter, "Recovery", lambda *_args: recovery)
    monkeypatch.setattr(adapter, "Docker", lambda *_args: engine)
    monkeypatch.setattr(adapter, "boot_id", lambda: BOOT)
    monkeypatch.setattr(adapter.shutil, "which", lambda _: "/trusted/docker")
    started = []
    clock = [0.0]
    monkeypatch.setattr(adapter.time, "monotonic", lambda: clock[0])

    def create(args, **kwargs):
        assert args[1:3] == ["--host=unix:///var/run/docker.sock", "create"]
        for flag in [
            "--runtime=runsc",
            "--network=none",
            "--read-only",
            "--log-driver=none",
            "--restart=no",
            "--no-healthcheck",
            "--user=65534:65534",
        ]:
            assert flag in args
        name = args[args.index("--name") + 1]
        label = args[args.index("--label") + 1]
        assert label.startswith(LABEL + "=")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 15)
        if failure == "creation":
            return subprocess.CompletedProcess(args, 1, b"", b"untrusted error")
        engine.create(name, label.split("=", 1)[1])
        return subprocess.CompletedProcess(
            args, 0, b"\xff" if failure == "encoding" else CID.encode(), b""
        )

    class Process:
        def __init__(self, args, **kwargs):
            assert args[2:] == ["start", "--attach", "--interactive", CID]
            with sqlite3.connect(recovery.path) as db:
                assert db.execute("SELECT container FROM containers").fetchone()[0] == CID
            started.append(CID)
            if failure == "launch":
                raise OSError("inert process launch failed")
            if failure == "cleanup":
                engine.offline = True
            if failure == "deadline":
                clock[0] = 100.0
            self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(b"{}"), io.BytesIO()
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, **kwargs):
            return self.returncode

    monkeypatch.setattr(adapter.subprocess, "run", create)
    monkeypatch.setattr(adapter.subprocess, "Popen", Process)
    runner = GVisorRunner("python@sha256:" + "a" * 64)
    if failure in {"creation", "timeout", "encoding"}:
        with pytest.raises(Failure, match="CLEANUP_UNKNOWN"):
            runner._execute(("python",), tmp_path, b"{}")
        assert not started
    elif failure == "launch":
        with pytest.raises(OSError):
            runner._execute(("python",), tmp_path, b"{}")
        assert engine.records  # Remains journal-owned for the independent service.
    else:
        result = runner._execute(("python",), tmp_path, b"{}")
        assert result.status == ("unknown" if failure in {"cleanup", "deadline"} else "reported")
        assert result.reason == {"cleanup": "cleanup_unknown", "deadline": "timeout"}.get(
            failure, "completed"
        )
        assert result.stdout == b"{}"


def test_unconfigured_recovery_refuses_host_launch(tmp_path, monkeypatch):
    import checkedflow.runner as adapter

    monkeypatch.delenv("CHECKEDFLOW_SANDBOX_RECOVERY", raising=False)
    monkeypatch.setattr(adapter.shutil, "which", lambda _: "/trusted/docker")
    monkeypatch.setattr(
        adapter.subprocess, "Popen", lambda *a, **k: pytest.fail("unexpected launch")
    )
    with pytest.raises(Failure, match="independent recovery journal required"):
        GVisorRunner("python@sha256:" + "a" * 64)._execute(("python",), tmp_path, b"{}")
