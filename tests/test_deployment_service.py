"""Explicit service requests preserve unknown outcomes and native signing material."""

import base64
import json
import os
from copy import deepcopy
from hashlib import sha256
from importlib.resources import files
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator
from test_deployment_verification import bundle

from checkedflow.cli import main
from checkedflow.core.values import Failure
from checkedflow.distributed import deployment_service as module


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    calls = []
    services = {
        role: {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "MainPID": "0"}
        for role in ("abci", "validator")
    }
    monkeypatch.setattr(module, "bound", lambda *args: ({}, {"name": "node0"}, "lab"))
    monkeypatch.setattr(module, "service_status", lambda name: deepcopy(services))
    monkeypatch.setattr(module, "inspect", lambda *args: calls.append("preflight"))
    monkeypatch.setattr(module, "identities", lambda *args: calls.append("identity") or "c" * 64)
    monkeypatch.setattr(module, "command", lambda args: calls.append(args) or "")
    monkeypatch.setattr(module, "empty_groups", lambda name: True)

    def operate(action, wheel=True):
        return module.operate(
            tmp_path, "a" * 64, tmp_path / "candidate.whl" if wheel else None, "node0", action
        )

    return operate, calls, services


def test_status_has_no_mutation_or_readiness_claim(lifecycle):
    operate, calls, _ = lifecycle
    value = operate("status", wheel=False)
    assert value["status"] == "OBSERVED" and value["request_issued"] is False
    assert value["consensus"]["status"] == "NOT_CHECKED"
    assert calls == []


@pytest.mark.parametrize("action", ["start", "status", "stop"])
def test_cli_action_does_not_replace_subcommand_dispatch(lifecycle, action, monkeypatch, capfd):
    operate, _, _ = lifecycle
    value = operate(action)
    schema = json.loads(
        files("checkedflow").joinpath("data/deployment-service.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(value)

    def invoke(directory, expected, wheel, node, selected):
        assert selected == action and node == "node0"
        return value

    monkeypatch.setattr(module, "operate", invoke)
    assert (
        main(
            [
                "deployment-service",
                "--directory",
                "review",
                "--expected-plan",
                "a" * 64,
                "--node",
                "node0",
                "--action",
                action,
                "--wheel",
                "candidate.whl",
            ]
        )
        == 0
    )
    assert json.loads(capfd.readouterr().out)["action"] == action


def test_start_checks_fresh_files_and_keys_before_one_request(lifecycle):
    operate, calls, _ = lifecycle
    value = operate("start")
    assert value["status"] == "START_REQUESTED"
    assert calls == [
        "preflight",
        "identity",
        ["/usr/bin/systemctl", "--no-block", "start", "checkedflow-lab-validator.service"],
    ]
    assert value["release_authority"] is False


@pytest.mark.parametrize("boundary", ["inspect", "identities"])
def test_rejected_start_does_not_issue_native_request(lifecycle, monkeypatch, boundary):
    operate, calls, _ = lifecycle

    def fail(*args):
        raise Failure("BINDING", "changed")

    monkeypatch.setattr(module, boundary, fail)
    with pytest.raises(Failure, match="BINDING"):
        operate("start")
    assert not any(isinstance(call, list) for call in calls)


def test_start_requires_wheel_but_stop_does_not(lifecycle):
    operate, calls, _ = lifecycle
    with pytest.raises(Failure, match="ARGUMENT"):
        operate("start", wheel=False)
    value = operate("stop", wheel=False)
    assert value["status"] == "STOPPED" and value["signing_state_sha256"] == "c" * 64
    assert "preflight" not in calls
    assert calls[0] == [
        "/usr/bin/systemctl",
        "--no-block",
        "stop",
        "checkedflow-lab-validator.service",
        "checkedflow-lab-abci.service",
    ]
    assert calls[1:] == ["identity"]


@pytest.mark.parametrize("action", ["start", "stop"])
def test_native_reply_loss_stays_unknown_without_retry(lifecycle, monkeypatch, action):
    operate, calls, _ = lifecycle

    def fail(arguments):
        calls.append(arguments)
        raise Failure("NATIVE", "reply lost")

    monkeypatch.setattr(module, "command", fail)
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        operate(action)
    assert len([call for call in calls if isinstance(call, list)]) == 1


@pytest.mark.parametrize("held", ["process", "cgroup"])
def test_stop_never_force_kills_or_reissues_when_not_confirmed(lifecycle, monkeypatch, held):
    operate, calls, services = lifecycle
    if held == "process":
        services["validator"]["MainPID"] = "77"
        services["validator"]["ActiveState"] = "deactivating"
    else:
        monkeypatch.setattr(module, "empty_groups", lambda name: False)
    ticks = iter([0, 100])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        operate("stop")
    assert len(calls) == 1 and "kill" not in calls[0]


def test_stop_preservation_failure_is_not_success(lifecycle, monkeypatch):
    operate, _, _ = lifecycle

    def missing(*args):
        raise OSError("missing signing state")

    monkeypatch.setattr(module, "identities", missing)
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        operate("stop")


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    seed = bytes([7]) * 32
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    value = {
        "address": sha256(public).digest()[:20].hex().upper(),
        "pub_key": {"type": "tendermint/PubKeyEd25519", "value": base64.b64encode(public).decode()},
        "priv_key": {
            "type": "tendermint/PrivKeyEd25519",
            "value": base64.b64encode(seed + public).decode(),
        },
    }
    path = tmp_path / "priv_validator_key.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(module, "protected", lambda *args, **kwargs: path)
    original = Path.stat

    def private_stat(target, **kwargs):
        values = list(original(target, **kwargs))
        if target == path:
            values[0] &= ~0o077
        return os.stat_result(values)

    monkeypatch.setattr(Path, "stat", private_stat)
    return path, value, public


def test_native_key_is_read_without_modification(key_file):
    path, _, public = key_file
    before = path.read_bytes()
    assert module.key(path, 1001) == public
    assert path.read_bytes() == before


def test_readable_private_key_is_rejected(key_file, monkeypatch):
    path, _, _ = key_file
    original = Path.stat

    def public_stat(target, **kwargs):
        values = list(original(target, **kwargs))
        values[0] |= 0o044
        return os.stat_result(values)

    monkeypatch.setattr(Path, "stat", public_stat)
    with pytest.raises(Failure, match="signing key permissions"):
        module.key(path, 1001)


@pytest.mark.parametrize(
    "change", ["missing_public", "wrong_address", "wrong_type", "short", "wrong_private_public"]
)
def test_native_key_mismatch_is_rejected(key_file, change):
    path, value, _ = key_file
    if change == "missing_public":
        del value["pub_key"]
    elif change == "wrong_address":
        value["address"] = "0" * 40
    elif change == "wrong_type":
        value["priv_key"]["type"] = "other"
    elif change == "short":
        value["priv_key"]["value"] = base64.b64encode(bytes(32)).decode()
    else:
        value["priv_key"]["value"] = base64.b64encode(bytes(64)).decode()
    path.write_text(json.dumps(value))
    with pytest.raises(Failure, match="CUSTODY"):
        module.key(path, 1001)


@pytest.mark.parametrize("change", ["none", "root", "node", "unit", "configuration"])
def test_bound_operator_plan_precedes_service_actions(tmp_path, monkeypatch, change):
    directory, approved, _, _ = bundle(tmp_path)
    monkeypatch.setattr(module, "host", lambda: None)
    monkeypatch.setattr(module, "protected", lambda path, **kw: path)
    original = module._read

    def read(path, limit):
        if path.suffix == ".service":
            return (
                b"modified unit"
                if change == "unit"
                else original(directory / "node0" / path.name, limit)
            )
        if path.name == "operational.json" and change == "configuration":
            return b"modified configuration"
        return original(path, limit)

    monkeypatch.setattr(module, "_read", read)
    args = (
        directory,
        "0" * 64 if change == "root" else approved,
        "foreign" if change == "node" else "node0",
    )
    if change == "none":
        _, node, name = module.bound(*args)
        assert node["name"] == "node0" and name == "test"
    else:
        with pytest.raises(Failure, match="BINDING"):
            module.bound(*args)
