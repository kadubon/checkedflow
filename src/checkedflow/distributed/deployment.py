"""Offline four-node configuration planning; no host changes or signing-key creation."""

import base64
import ipaddress
import re
from datetime import datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import cast

from checkedflow.core.operational import genesis
from checkedflow.core.values import JSON, Failure, Object, array, fields, obj, require, text
from checkedflow.distributed.operational_application import Configuration
from checkedflow.wire import document, dumps

MAX_INPUT = 4_194_304


def _match(value: JSON, pattern: str, label: str) -> str:
    spelling = text(value, limit=256)
    require(re.fullmatch(pattern, spelling) is not None, "DEPLOYMENT", label)
    return spelling


def _path(value: JSON) -> str:
    spelling = _match(value, r"/[A-Za-z0-9_./-]+", "unsafe executable path")
    require(
        spelling.startswith(("/opt/", "/usr/"))
        and not spelling.startswith("//")
        and str(PurePosixPath(spelling)) == spelling
        and ".." not in PurePosixPath(spelling).parts
        and spelling != "/",
        "DEPLOYMENT",
        "executable path must be absolute and normalized",
    )
    return spelling


def _inventory(raw: bytes) -> Object:
    require(len(raw) <= 65536, "LIMIT", "inventory byte limit")
    value = document(raw)
    fields(value, "version name runtime nodes")
    require(value["version"] == "checkedflow/deployment/v1", "VERSION", "deployment profile")
    _match(value["name"], r"[a-z][a-z0-9-]{0,11}", "deployment name")
    runtime = obj(value["runtime"])
    fields(runtime, "python cometbft cometbft_sha256 wheel_sha256")
    require(_path(runtime["python"]) != _path(runtime["cometbft"]), "DEPLOYMENT", "runtime paths")
    for key in ("cometbft_sha256", "wheel_sha256"):
        _match(runtime[key], r"[0-9a-f]{64}", "runtime SHA-256")
    nodes = [obj(item) for item in array(value["nodes"], limit=4)]
    require(len(nodes) == 4, "DEPLOYMENT", "four nodes required")
    for node in nodes:
        fields(node, "name organization address node_id")
        _match(node["name"], r"[a-z][a-z0-9-]{0,31}", "node name")
        require(
            node["name"]
            not in {
                "con",
                "prn",
                "aux",
                "nul",
                *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10)),
            },
            "DEPLOYMENT",
            "portable node directory name required",
        )
        text(node["organization"], limit=80)
        _match(node["node_id"], r"[0-9a-f]{40}", "CometBFT P2P identity")
        address = text(node["address"], limit=15)
        try:
            ip = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError:
            raise Failure("DEPLOYMENT", "private IPv4 address required") from None
        require(
            any(
                ip in ipaddress.IPv4Network(net)
                for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            ),
            "DEPLOYMENT",
            "explicit RFC1918 peer address required",
        )
    for key in ("name", "organization", "address", "node_id"):
        require(
            len({text(node[key]) for node in nodes}) == 4, "DEPLOYMENT", "duplicate node binding"
        )
    return value


def _bind(configuration: Configuration, raw: bytes, nodes: list[Object]) -> bytes:
    require(len(raw) <= MAX_INPUT, "LIMIT", "genesis byte limit")
    initial = configuration.initial
    require(
        initial
        == genesis(initial.chain, initial.mission, initial.organizations, initial.credentials),
        "DEPLOYMENT",
        "fresh genesis required; migration needs separate custody and retention deployment",
    )
    require(
        tuple(node["organization"] for node in nodes) == initial.organizations,
        "DEPLOYMENT",
        "inventory organization order differs",
    )
    reserved_ids = {
        sha256(bytes.fromhex(public)).digest()[:20].hex()
        for public in (
            *(key for _, key in configuration.validators),
            *(credential.public_key for credential in initial.credentials),
        )
    }
    require(
        not reserved_ids.intersection(text(node["node_id"]) for node in nodes),
        "GENESIS",
        "P2P, validator and command keys must be separate",
    )
    value = document(raw)
    fields(
        value, "genesis_time chain_id initial_height consensus_params validators app_hash app_state"
    )
    require(
        value["chain_id"] == initial.chain
        and value["initial_height"] == "1"
        and value["app_hash"] == ""
        and value["app_state"] == configuration.encode(),
        "GENESIS",
        "consensus genesis differs from reviewed application configuration",
    )
    timestamp = text(value["genesis_time"], limit=40)
    require(
        re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z", timestamp
        )
        is not None,
        "GENESIS",
        "explicit RFC3339 UTC genesis time required",
    )
    try:
        datetime.fromisoformat(timestamp)
    except ValueError:
        require(False, "GENESIS", "invalid genesis time")
    obj(value["consensus_params"])
    keys, powers = set(), set()
    for item in array(value["validators"], limit=4):
        row = obj(item)
        fields(row, "address pub_key power name")
        public = obj(row["pub_key"])
        fields(public, "type value")
        require(public["type"] == "tendermint/PubKeyEd25519", "GENESIS", "validator algorithm")
        spelling = text(public["value"], limit=44)
        try:
            key = base64.b64decode(spelling, validate=True)
        except ValueError:
            raise Failure("GENESIS", "validator public-key encoding") from None
        require(
            len(key) == 32
            and base64.b64encode(key).decode("ascii") == spelling
            and row["address"] == sha256(key).digest()[:20].hex().upper(),
            "GENESIS",
            "validator address/key binding",
        )
        keys.add(key.hex())
        powers.add(_match(row["power"], r"[1-9][0-9]{0,14}", "positive voting power"))
    require(
        len(keys) == 4
        and keys == {key for _, key in configuration.validators}
        and len(powers) == 1,
        "GENESIS",
        "four matching equal-power validators required",
    )
    return dumps(value)


def render(inventory: bytes, configuration: bytes, consensus_genesis: bytes) -> dict[str, bytes]:
    """Return review files; declared runtime hashes still need live preflight verification."""
    require(len(configuration) <= MAX_INPUT, "LIMIT", "configuration byte limit")
    inv = _inventory(inventory)
    config = Configuration.decode(configuration)
    nodes = [obj(item) for item in array(inv["nodes"])]
    genesis_bytes = _bind(config, consensus_genesis, nodes)
    name = text(inv["name"])
    runtime = obj(inv["runtime"])
    root, etc = f"/var/lib/checkedflow/{name}", f"/etc/checkedflow/{name}"
    app_unit, validator_unit = (
        f"checkedflow-{name}-abci.service",
        f"checkedflow-{name}-validator.service",
    )
    result: dict[str, bytes] = {}
    for node in nodes:
        folder = text(node["name"])
        peers = ",".join(
            f"{peer['node_id']}@{peer['address']}:26656" for peer in nodes if peer != node
        )
        toml = f'''version = "0.40.0"
proxy_app = "tcp://127.0.0.1:26658"
abci = "grpc"
moniker = "{folder}"
genesis_file = "{etc}/genesis.json"
priv_validator_key_file = "config/priv_validator_key.json"
priv_validator_state_file = "data/priv_validator_state.json"
node_key_file = "config/node_key.json"
[rpc]
laddr = "tcp://127.0.0.1:26657"
unsafe = false
grpc_laddr = ""
pprof_laddr = ""
[p2p]
laddr = "tcp://{node["address"]}:26656"
external_address = "{node["address"]}:26656"
persistent_peers = "{peers}"
seeds = ""
pex = false
addr_book_strict = false
max_num_outbound_peers = 0
allow_duplicate_ip = false
[instrumentation]
prometheus = false
'''
        app = _unit(
            f"cf-{name}-app",
            f"{root}/application",
            f"{runtime['python']} -I -m checkedflow.distributed.operational_application"
            f" --configuration {etc}/operational.json --database {root}/application/state.sqlite"
            " --address 127.0.0.1:26658",
            f"ConditionPathExists={etc}/operational.json\n",
            "",
            ["127.0.0.0/8"],
        )
        validator = _unit(
            f"cf-{name}-val",
            f"{root}/validator",
            "/usr/bin/flock --exclusive --nonblock --conflict-exit-code 73 --no-fork"
            f" {root}/validator/custody.lock {runtime['cometbft']} start --home {root}/validator",
            f"After={app_unit}\nRequires={app_unit}\n"
            f"ConditionPathExists={root}/validator/config/priv_validator_key.json\n"
            f"ConditionPathExists={root}/validator/data/priv_validator_state.json\n"
            f"ConditionPathExists={root}/validator/config/node_key.json\n",
            "RestartPreventExitStatus=73\n",
            ["127.0.0.0/8", *(text(peer["address"]) for peer in nodes)],
        )
        for filename, body in {
            "config.toml": toml.encode(),
            app_unit: app,
            validator_unit: validator,
            "operational.json": dumps(config.encode()),
            "genesis.json": genesis_bytes,
        }.items():
            result[f"{folder}/{filename}"] = body
    manifest: Object = {
        "version": "checkedflow/deployment-plan/v1",
        "inventory": inv,
        "inputs": {
            "inventory": sha256(inventory).hexdigest(),
            "configuration": sha256(configuration).hexdigest(),
            "genesis": sha256(consensus_genesis).hexdigest(),
        },
        "files": cast(Object, {path: sha256(body).hexdigest() for path, body in result.items()}),
        "status": "REVIEW_REQUIRED",
        "runtime_verified": False,
        "hosts_changed": False,
    }
    result["plan.json"] = dumps(manifest)
    return result


def _unit(
    user: str, directory: str, command: str, conditions: str, service: str, addresses: list[str]
) -> bytes:
    return (
        "[Unit]\nDescription=CheckedFlow reviewed node service\n"
        + conditions
        + "\n[Service]\nType=simple\n"
        + f"User={user}\nGroup={user}\n"
        + f"WorkingDirectory={directory}\nExecStart={command}\n"
        + "UMask=0077\nRestart=on-failure\nRestartSec=2\nKillMode=control-group\n"
        + "TimeoutStopSec=60\nSendSIGKILL=no\nNoNewPrivileges=true\nPrivateTmp=true\n"
        + "ProtectHome=true\nProtectSystem=strict\n"
        + f"ReadWritePaths={directory}\n"
        + "IPAddressDeny=any\n"
        + "".join(f"IPAddressAllow={address}\n" for address in addresses)
        + service
        + "\n[Install]\nWantedBy=multi-user.target\n"
    ).encode()


def write_new(directory: Path, output: dict[str, bytes]) -> str:
    """Preserve failures in a new private directory; never overwrite an existing plan."""
    require("plan.json" in output, "DEPLOYMENT", "plan manifest missing")
    for name in output:
        path = PurePosixPath(name)
        require(
            name not in {"", "."}
            and not path.is_absolute()
            and ".." not in path.parts
            and str(path) == name
            and "\\" not in name
            and ":" not in name,
            "PATH",
            "relative output path required",
        )
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name in sorted(output, key=lambda item: (item == "plan.json", item)):
        target = directory / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as stream:
            target.chmod(0o600)
            stream.write(output[name])
    return sha256(output["plan.json"]).hexdigest()
