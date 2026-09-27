"""Offline plans bind four public identities without creating private signer material."""

import base64
import json
import tomllib
from copy import deepcopy
from hashlib import sha256
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from test_work_tasks import Harness

from checkedflow.cli import main
from checkedflow.core.values import Failure
from checkedflow.distributed.deployment import MAX_INPUT, render, write_new
from checkedflow.distributed.operational_application import Configuration
from checkedflow.wire import document, dumps


def inputs():
    harness = Harness()
    config = Configuration(
        harness.initial,
        tuple(
            (org, sha256(("deployment-validator-" + org).encode()).hexdigest())
            for org in harness.initial.organizations
        ),
    )
    inventory = {
        "version": "checkedflow/deployment/v1",
        "name": "test",
        "runtime": {
            "python": "/opt/checkedflow/bin/python",
            "cometbft": "/opt/checkedflow/cometbft",
            "cometbft_sha256": "a" * 64,
            "wheel_sha256": "b" * 64,
        },
        "nodes": [
            {
                "name": f"node{i}",
                "organization": org,
                "address": f"10.23.0.{i + 1}",
                "node_id": sha256(("p2p-" + org).encode()).hexdigest()[:40],
            }
            for i, org in enumerate(harness.initial.organizations)
        ],
    }
    validators = []
    for org, public in config.validators:
        key = bytes.fromhex(public)
        validators.append(
            {
                "name": org,
                "address": sha256(key).digest()[:20].hex().upper(),
                "power": "10",
                "pub_key": {
                    "type": "tendermint/PubKeyEd25519",
                    "value": base64.b64encode(key).decode(),
                },
            }
        )
    # Native CometBFT validation is a separate installed test, not implied by these fixtures.
    consensus = {
        "genesis_time": "2026-01-01T00:00:00Z",
        "chain_id": harness.initial.chain,
        "initial_height": "1",
        "app_hash": "",
        "consensus_params": {},
        "validators": validators,
        "app_state": config.encode(),
    }
    return inventory, config.encode(), consensus


def plan(values):
    return render(*(dumps(value) for value in values))


def test_deterministic_plan_binds_nodes_without_keys_or_public_management(tmp_path):
    values = inputs()
    output = plan(values)
    assert output == plan(deepcopy(values)) and len(output) == 21
    manifest = document(output["plan.json"])
    assert manifest["hosts_changed"] is False and manifest["runtime_verified"] is False
    assert manifest["files"] == {
        name: sha256(body).hexdigest() for name, body in output.items() if name != "plan.json"
    }
    for index, node in enumerate(values[0]["nodes"]):
        config = tomllib.loads(output[f"node{index}/config.toml"].decode())
        assert config["abci"] == "grpc"
        assert config["proxy_app"] == "tcp://127.0.0.1:26658"
        assert config["rpc"]["laddr"] == "tcp://127.0.0.1:26657"
        assert not config["rpc"]["unsafe"] and not config["p2p"]["pex"]
        peers = config["p2p"]["persistent_peers"].split(",")
        assert len(peers) == 3 and all(node["node_id"] not in peer for peer in peers)
        unit = output[f"node{index}/checkedflow-test-validator.service"].decode()
        assert "--no-fork" in unit and "RestartPreventExitStatus=73" in unit
        assert "SendSIGKILL=no" in unit and "IPAddressDeny=any" in unit
        assert "User=cf-test-val" in unit
        assert not any(name.endswith(".key") or "priv_validator" in name for name in output)
    destination = tmp_path / "review"
    assert write_new(destination, output) == sha256(output["plan.json"]).hexdigest()
    assert all((destination / name).read_bytes() == body for name, body in output.items())
    with pytest.raises(FileExistsError):
        write_new(destination, output)
    schema = json.loads(
        files("checkedflow").joinpath("data/deployment-inventory.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(values[0])


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "unknown"),
        ("name", "../escape"),
        ("name", "x\nExecStart=/bin/false"),
    ],
)
def test_inventory_header_rejections(field, value):
    values = inputs()
    values[0][field] = value
    with pytest.raises(Failure):
        plan(values)


@pytest.mark.parametrize(
    "path",
    [
        "/var/not-installed/python",
        "/opt/../bin/python",
        "/opt/x//python",
        "/opt/x/./python",
        "/opt/x/python/",
        "/opt/x/$python",
        "/opt/x/%python",
        "/opt/x/python --flag",
        "relative",
        "/",
    ],
)
def test_executable_paths_cannot_inject_unit_arguments(path):
    values = inputs()
    values[0]["runtime"]["python"] = path
    with pytest.raises(Failure):
        plan(values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("address", "127.0.0.1"),
        ("address", "8.8.8.8"),
        ("address", "::1"),
        ("address", "not-an-address"),
        ("name", "../node"),
        ("name", "con"),
        ("name", "nul"),
        ("name", "com1"),
        ("name", "lpt9"),
        ("node_id", "x" * 40),
        ("organization", "other"),
    ],
)
def test_peer_bindings_are_explicit(field, value):
    values = inputs()
    values[0]["nodes"][0][field] = value
    with pytest.raises(Failure):
        plan(values)


@pytest.mark.parametrize("field", ["name", "organization", "address", "node_id"])
def test_duplicate_bindings_and_reused_key_purposes_rejected(field):
    values = inputs()
    values[0]["nodes"][0][field] = values[0]["nodes"][1][field]
    with pytest.raises(Failure):
        plan(values)
    values = inputs()
    values[0]["nodes"][0]["node_id"] = values[2]["validators"][0]["address"].lower()
    with pytest.raises(Failure, match="separate"):
        plan(values)


@pytest.mark.parametrize(
    "change",
    ["chain", "power", "address", "public", "algorithm", "time", "state", "members", "initialized"],
)
def test_mismatched_consensus_genesis_rejected(change):
    inv, config, consensus = inputs()
    if change == "chain":
        consensus["chain_id"] = "foreign"
    elif change == "power":
        consensus["validators"][0]["power"] = "11"
    elif change == "address":
        consensus["validators"][0]["address"] = "0" * 40
    elif change == "public":
        consensus["validators"][0]["pub_key"]["value"] = "!"
    elif change == "algorithm":
        consensus["validators"][0]["pub_key"]["type"] = "unknown"
    elif change == "time":
        consensus["genesis_time"] = "2026-02-31T00:00:00Z"
    elif change == "state":
        consensus["app_state"] = {}
    elif change == "members":
        consensus["validators"].pop()
    else:
        config["state"]["height"] = 1
    with pytest.raises(Failure):
        plan((inv, config, consensus))


def test_input_limits_and_output_traversal_leave_no_destination(tmp_path):
    values = [dumps(value) for value in inputs()]
    for index, size in ((0, 65537), (1, MAX_INPUT + 1), (2, MAX_INPUT + 1)):
        bad = values.copy()
        bad[index] = b" " * size
        with pytest.raises(Failure, match="LIMIT"):
            render(*bad)
    for name in ("../escape", "/absolute", ".", "", "a\\b", "a:b"):
        with pytest.raises(Failure, match="PATH"):
            write_new(tmp_path / "absent", {"plan.json": b"{}", name: b"bad"})
        assert not (tmp_path / "absent").exists()


def test_cli_shape_is_review_only(tmp_path, capfd):
    arguments = ["deployment-plan"]
    for name, value in zip(("inventory", "configuration", "genesis"), inputs(), strict=True):
        filename = tmp_path / (name + ".json")
        filename.write_bytes(dumps(value))
        arguments.extend(("--" + name, str(filename)))
    assert main([*arguments, "--destination", str(tmp_path / "output")]) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["status"] == "REVIEW_REQUIRED" and result["hosts_changed"] is False
    assert result["files"] == 21 and len(result["plan_sha256"]) == 64
