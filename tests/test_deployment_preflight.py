"""Host checks are observations; simulated boundaries do not qualify native deployment."""

import json
import os
import subprocess
import sys
import zipfile
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from test_deployment_verification import bundle

import checkedflow
from checkedflow.core.values import Failure
from checkedflow.distributed import deployment_preflight as module
from checkedflow.wire import document, dumps


@pytest.fixture
def host_files(tmp_path, monkeypatch):
    directory, approved, wheel, binary = bundle(tmp_path)
    manifest = document((directory / "plan.json").read_bytes())
    manifest["inventory"]["runtime"]["python"] = sys.executable
    (directory / "plan.json").write_bytes(dumps(manifest))
    monkeypatch.setattr(module, "host", lambda: None)
    monkeypatch.setattr(module, "validator_uid", lambda name: 1001)
    monkeypatch.setattr(module, "account_ids", lambda name: (1002, 1001))
    monkeypatch.setattr(module, "protected", lambda path, **kw: Path(sys.executable).resolve())
    monkeypatch.setattr(module, "installed_members", lambda path: 196)
    monkeypatch.setattr(
        module, "verify", lambda *args: {"plan_sha256": approved, "wheel_sha256": "b" * 64}
    )
    original = module._read

    def read(path, limit):
        if path == directory / "plan.json":
            return original(path, limit)
        return original(directory / "node0" / path.name, limit)

    monkeypatch.setattr(module, "_read", read)
    responses = []

    def native(arguments):
        if arguments[0] == "/usr/bin/go":
            return "\tmod\t" + "\t".join(module.COMET_MODULE)
        if arguments[-1] == "--verbose":
            return json.dumps(
                {"cometbft": "0.39.0", "abci": "2.0.0", "block_protocol": 11, "p2p_protocol": 8}
            )
        unit = arguments[2]
        account = "cf-test-app" if "abci" in unit else "cf-test-val"
        response = {
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "SubState": "dead",
            "MainPID": "0",
            "FragmentPath": "/etc/systemd/system/" + unit,
            "DropInPaths": "",
            "NeedDaemonReload": "no",
            "User": account,
            "Group": account,
        }
        if responses:
            response.update(responses[0])
        return "\n".join(f"{key}={value}" for key, value in response.items())

    monkeypatch.setattr(module, "command", native)
    return directory, approved, wheel, responses, manifest


def test_host_observation_never_authorizes_startup(host_files):
    directory, approved, wheel, _, _ = host_files
    value = module.inspect(directory, approved, wheel, "node0")
    assert value["status"] == "HOST_FILES_VERIFIED"
    assert value["startup_authorized"] is False and value["hosts_changed"] is False
    assert len(value["remaining"]) == 4 and value["installed_package_members"] == 196
    schema = json.loads(
        files("checkedflow").joinpath("data/deployment-preflight.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(value)


@pytest.mark.parametrize(
    "change",
    [
        {"LoadState": "not-found"},
        {"DropInPaths": "/unexpected.conf"},
        {"NeedDaemonReload": "yes"},
        {"User": "root"},
        {"Group": "root"},
        {"FragmentPath": "/another.service"},
    ],
)
def test_loaded_unit_overrides_or_stale_configuration_fail(host_files, change):
    directory, approved, wheel, responses, _ = host_files
    responses.append(change)
    with pytest.raises(Failure, match="SERVICE"):
        module.inspect(directory, approved, wheel, "node0")


def test_unknown_node_and_changed_host_file_fail(host_files, monkeypatch):
    directory, approved, wheel, _, _ = host_files
    with pytest.raises(Failure, match="BINDING"):
        module.inspect(directory, approved, wheel, "foreign")
    original = module._read
    monkeypatch.setattr(
        module,
        "_read",
        lambda p, limit: b"changed" if p.name == "config.toml" else original(p, limit),
    )
    with pytest.raises(Failure, match="installed configuration differs"):
        module.inspect(directory, approved, wheel, "node0")


def test_native_version_is_not_implied_by_a_hash(host_files, monkeypatch):
    directory, approved, wheel, _, _ = host_files
    monkeypatch.setattr(module, "command", lambda argv: "0.39.0")
    with pytest.raises(Failure, match="VERSION"):
        module.inspect(directory, approved, wheel, "node0")


def test_native_module_pin_matches_provisioning_lock():
    lock = json.loads((Path(__file__).parents[1] / "deploy/runtime-lock.json").read_text())[
        "cometbft"
    ]
    assert (lock["module"], lock["version"], lock["module_sum"]) == module.COMET_MODULE


def test_installed_package_bytes_and_unexpected_source(tmp_path, monkeypatch):
    package = tmp_path / "checkedflow"
    package.mkdir()
    (package / "__init__.py").write_bytes(b"original")
    wheel = tmp_path / "package.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("checkedflow/__init__.py", b"original")
    monkeypatch.setattr(checkedflow, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(module.sysconfig, "get_path", lambda _: str(tmp_path))
    monkeypatch.setattr(module, "protected", lambda path: path)
    assert module.installed_members(wheel) == 1
    (package / "unapproved.py").write_bytes(b"extra")
    with pytest.raises(Failure, match="unapproved package file"):
        module.installed_members(wheel)
    (package / "unapproved.py").unlink()
    (package / "__init__.py").write_bytes(b"changed")
    with pytest.raises(Failure, match="installed bytes differ"):
        module.installed_members(wheel)


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired("command", 15), subprocess.CalledProcessError(1, "command")]
)
def test_native_failure_omits_process_output(monkeypatch, error):
    monkeypatch.setattr(module, "protected", lambda path, **kw: path)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(module.subprocess, "run", fail)
    with pytest.raises(Failure, match="NATIVE: native inspection failed or timed out"):
        module.command(["/usr/bin/systemctl", "show"])


def test_native_execution_is_bounded_and_environment_does_not_leak(monkeypatch):
    monkeypatch.setattr(module, "protected", lambda path, **kw: path)

    def run(argv, **kwargs):
        assert kwargs["timeout"] == 15 and kwargs["cwd"] == "/"
        assert set(kwargs["env"]) == {"PATH", "LANG", "LC_ALL"}
        return SimpleNamespace(stdout=b"0.40.0\n", stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.command(["/opt/checkedflow/cometbft", "version"]) == "0.40.0"


@pytest.mark.parametrize("uid,mode", [(1001, 0o755), (0, 0o775), (0, 0o757)])
def test_protected_ancestry_rejects_foreign_owner_or_write_permission(
    tmp_path, monkeypatch, uid, mode
):
    original = Path.lstat

    def observed(path):
        values = list(original(path))
        values[0] = (values[0] & ~0o777) | (mode if path == tmp_path else 0o755)
        values[4] = uid if path == tmp_path else 0
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", observed)
    with pytest.raises(Failure, match="CUSTODY"):
        module.protected(tmp_path)


def test_platform_rejected_before_host_paths(monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "unsupported")
    with pytest.raises(Failure, match="PLATFORM"):
        module.host()


@pytest.mark.parametrize("uid,isolated", [(1001, 1), (0, 0)])
def test_root_and_isolation_are_both_required(monkeypatch, uid, isolated):
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.os, "geteuid", lambda: uid, raising=False)
    monkeypatch.setattr(module.sys, "flags", SimpleNamespace(isolated=isolated))
    with pytest.raises(Failure, match="CUSTODY"):
        module.host()


@pytest.mark.parametrize("identity", [0, None, 1001])
def test_validator_account_must_exist_and_be_nonroot(monkeypatch, identity):
    def lookup(name):
        assert name == "cf-test-val"
        if identity is None:
            raise KeyError(name)
        return SimpleNamespace(pw_uid=identity)

    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lookup))
    if identity == 1001:
        assert module.validator_uid("test") == 1001
    else:
        with pytest.raises(Failure, match="CUSTODY"):
            module.validator_uid("test")


@pytest.mark.parametrize("change", ["none", "alias", "root", "group", "missing"])
def test_service_accounts_have_distinct_nonroot_ids_and_matching_groups(monkeypatch, change):
    def user(name):
        if change == "missing":
            raise KeyError(name)
        uid = 1001 if name.endswith("app") or change == "alias" else 1002
        return SimpleNamespace(pw_uid=0 if change == "root" else uid, pw_gid=uid)

    def group(name):
        return SimpleNamespace(
            gr_gid=1001 if name.endswith("app") or change == "alias" or change == "group" else 1002
        )

    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=user))
    monkeypatch.setitem(sys.modules, "grp", SimpleNamespace(getgrnam=group))
    if change == "none":
        assert module.account_ids("test") == (1001, 1002)
    else:
        with pytest.raises(Failure, match="CUSTODY"):
            module.account_ids("test")


@pytest.mark.parametrize("change", ["replacement", "protocol"])
def test_module_replacement_and_wrong_native_protocol_fail(host_files, monkeypatch, change):
    directory, approved, wheel, _, _ = host_files
    native = module.command

    def altered(arguments):
        result = native(arguments)
        if change == "replacement" and arguments[0] == "/usr/bin/go":
            return result + "\n=>\tlocal-copy"
        if change == "protocol" and arguments[-1] == "--verbose":
            value = json.loads(result)
            value["abci"] = "1.0.0"
            return json.dumps(value)
        return result

    monkeypatch.setattr(module, "command", altered)
    with pytest.raises(Failure, match="VERSION"):
        module.inspect(directory, approved, wheel, "node0")


@pytest.mark.parametrize(
    "mode,parent_mode", [(0o700, 0o755), (0o744, 0o755), (0o755, 0o755), (0o755, 0o700)]
)
def test_service_runtime_requires_read_and_execute(tmp_path, monkeypatch, mode, parent_mode):
    target = tmp_path / "runtime"
    target.write_bytes(b"fixture")
    original_lstat, original_stat = Path.lstat, Path.stat

    def observed(path, *, follow_symlinks=True):
        values = list(original_stat(path, follow_symlinks=follow_symlinks))
        values[0] = (values[0] & ~0o777) | (mode if path == target else 0o755)
        values[4] = 0
        return os.stat_result(values)

    def ancestry(path):
        values = list(original_lstat(path))
        values[0] = (values[0] & ~0o777) | (parent_mode if path == tmp_path else 0o755)
        values[4] = 0
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", ancestry)
    monkeypatch.setattr(Path, "stat", observed)
    if mode == parent_mode == 0o755:
        assert module.protected(target, executable=True) == target.resolve()
    else:
        with pytest.raises(Failure, match="PERMISSION"):
            module.protected(target, executable=True)
