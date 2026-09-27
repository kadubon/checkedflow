"""Fresh file installation is explicit, non-overwriting and separate from service startup."""

import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest
from test_deployment_verification import bundle

from checkedflow.core.values import Failure
from checkedflow.distributed import deployment_apply as module
from checkedflow.distributed.deployment_lock import exclusive


@pytest.fixture
def provisioned(tmp_path, monkeypatch):
    directory, approved, wheel, binary = bundle(tmp_path)
    root = tmp_path / "host"
    original_path, original_stat = Path, Path.stat

    def path(value):
        spelling = str(value)
        if spelling.startswith(("/etc/", "/var/", "/opt/")):
            return root / spelling.lstrip("/")
        return original_path(value)

    for name in (
        "/etc/checkedflow/test",
        "/etc/systemd/system",
        "/var/lib/checkedflow/test/application",
        "/var/lib/checkedflow/test/validator/config",
        "/opt/checkedflow",
    ):
        path(name).mkdir(parents=True, exist_ok=True)
    path("/opt/checkedflow/cometbft").write_bytes(binary.read_bytes())

    def account_stat(target, **kwargs):
        result = list(original_stat(target, **kwargs))
        if target == path("/var/lib/checkedflow/test/application"):
            result[4] = 1001
        if target == path("/var/lib/checkedflow/test/validator"):
            result[4] = 1002
        return os.stat_result(result)

    monkeypatch.setattr(module, "Path", path)
    monkeypatch.setattr(Path, "stat", account_stat)
    monkeypatch.setattr(module, "host", lambda: None)
    monkeypatch.setattr(module, "protected", lambda path, **kw: path)
    monkeypatch.setattr(module, "runtime_check", lambda *args: {})
    monkeypatch.setattr(module, "account_ids", lambda name: (1001, 1002))
    monkeypatch.setattr(module, "identities", lambda *args: "a" * 64)
    monkeypatch.setattr(module, "empty_groups", lambda name: True)
    monkeypatch.setattr(module, "exclusive", lambda *args, **kw: nullcontext())
    calls = []

    def native(arguments):
        calls.append(arguments)
        if arguments[0] == "/usr/bin/stat":
            return "ext2/ext3"
        if "show" in arguments:
            return "ActiveState=inactive\nMainPID=0\nJob="
        return ""

    def install(target, body):
        if target.exists():
            assert target.read_bytes() == body
            return False
        target.write_bytes(body)
        return True

    monkeypatch.setattr(module, "command", native)
    monkeypatch.setattr(module, "install_new", install)
    monkeypatch.setattr(module, "inspect", lambda *args: {"status": "HOST_FILES_VERIFIED"})
    return directory, approved, wheel, path, calls


def test_apply_is_idempotent_and_never_starts_services(provisioned):
    directory, approved, wheel, _, calls = provisioned
    first = module.apply(directory, approved, wheel, "node0")
    assert first["files_created"] == 5 and first["services_started"] is False
    second = module.apply(directory, approved, wheel, "node0")
    assert second["files_created"] == 0 and second["files_verified"] == 5
    assert not any("start" in call or "enable" in call or "stop" in call for call in calls)


def test_all_conflicts_checked_before_any_file_installation(provisioned):
    directory, approved, wheel, path, calls = provisioned
    old = path("/etc/systemd/system/checkedflow-test-validator.service")
    old.write_bytes(b"different existing unit")
    with pytest.raises(Failure, match="CONFLICT"):
        module.apply(directory, approved, wheel, "node0")
    assert old.read_bytes() == b"different existing unit"
    assert not path("/etc/checkedflow/test/genesis.json").exists()
    assert not any("daemon-reload" in call for call in calls)


def test_busy_services_cannot_be_applied(provisioned, monkeypatch):
    directory, approved, wheel, path, _ = provisioned
    monkeypatch.setattr(module, "quiescent", lambda name: module.require(False, "BUSY", "active"))
    with pytest.raises(Failure, match="BUSY"):
        module.apply(directory, approved, wheel, "node0")
    assert not path("/etc/checkedflow/test/genesis.json").exists()


@pytest.mark.parametrize("change", ["active", "pid", "job", "cgroup"])
def test_quiescence_requires_no_job_process_or_populated_group(monkeypatch, change):
    values = {"ActiveState": "inactive", "MainPID": "0", "Job": ""}
    if change == "active":
        values["ActiveState"] = "activating"
    if change == "pid":
        values["MainPID"] = "7"
    if change == "job":
        values["Job"] = "77"
    monkeypatch.setattr(
        module, "command", lambda argv: "\n".join(f"{k}={v}" for k, v in values.items())
    )
    monkeypatch.setattr(module, "empty_groups", lambda name: change != "cgroup")
    with pytest.raises(Failure, match="BUSY"):
        module.quiescent("test")


def test_reload_uncertainty_preserves_applied_files(provisioned, monkeypatch):
    directory, approved, wheel, path, _ = provisioned
    native = module.command

    def command(arguments):
        if "daemon-reload" in arguments:
            raise Failure("NATIVE", "lost response")
        return native(arguments)

    monkeypatch.setattr(module, "command", command)
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        module.apply(directory, approved, wheel, "node0")
    assert path("/etc/checkedflow/test/genesis.json").is_file()
    monkeypatch.setattr(module, "command", native)
    assert module.apply(directory, approved, wheel, "node0")["files_created"] == 0


@pytest.mark.skipif(sys.platform != "linux", reason="Linux directory-descriptor contract")
def test_native_nonoverwriting_install_and_symlink_parent(tmp_path):
    target = tmp_path / "public.json"
    assert module.install_new(target, b"approved") is True
    assert module.install_new(target, b"approved") is False
    with pytest.raises(Failure, match="CONFLICT"):
        module.install_new(target, b"different")
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        module.install_new(linked / "unapproved.json", b"foreign")
    assert not (tmp_path / "unapproved.json").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux FIFO opening contract")
def test_fifo_destination_is_rejected_without_waiting_for_a_writer(tmp_path):
    target = tmp_path / "fifo"
    os.mkfifo(target)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path;"
            "from checkedflow.distributed.deployment_apply import install_new;"
            "import sys;install_new(Path(sys.argv[1]), b'public')",
            str(target),
        ],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode != 0 and b"regular destination" in result.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="Linux descriptor locking contract")
def test_native_lock_exclusion_and_inode_retention(tmp_path, monkeypatch):
    from checkedflow.distributed import deployment_lock

    monkeypatch.setattr(deployment_lock, "protected", lambda path, **kw: path)
    path = tmp_path / "custody.lock"
    owner = os.getuid()
    with exclusive(path, owner, create=True):
        inode = path.stat().st_ino
        with pytest.raises(Failure, match="BUSY"), exclusive(path, owner):
            pytest.fail("second owner acquired an exclusive lock")
    with exclusive(path, owner):
        assert path.stat().st_ino == inode and path.read_bytes() == b""
