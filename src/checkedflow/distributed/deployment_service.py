"""Explicit local systemd lifecycle for an already provisioned, approved node."""

import base64
import re
import time
from hashlib import sha256
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from checkedflow.core.values import Failure, Object, array, obj, require, text
from checkedflow.distributed.deployment import _inventory
from checkedflow.distributed.deployment_preflight import (
    command,
    host,
    inspect,
    protected,
    service_status,
    validator_uid,
)
from checkedflow.distributed.deployment_verification import _hash, _read
from checkedflow.distributed.operational_application import Configuration
from checkedflow.wire import document, dumps


def bound(directory: Path, expected: str, node: str) -> tuple[Object, Object, str]:
    host()
    protected(directory)
    raw = _read(directory / "plan.json", 131072)
    require(sha256(raw).hexdigest() == _hash(expected), "BINDING", "independent plan differs")
    manifest = document(raw)
    require(manifest.get("version") == "checkedflow/deployment-plan/v1", "VERSION", "plan profile")
    inventory = _inventory(dumps(manifest.get("inventory")))
    matches = [obj(row) for row in array(inventory["nodes"]) if obj(row)["name"] == node]
    require(len(matches) == 1, "BINDING", "node absent from reviewed plan")
    name = text(inventory["name"])
    configuration = _read(directory / node / "operational.json", 4194304)
    require(
        sha256(configuration).hexdigest()
        == obj(manifest.get("files")).get(f"{node}/operational.json"),
        "BINDING",
        "reviewed configuration bytes differ",
    )
    for role in ("abci", "validator"):
        unit = f"checkedflow-{name}-{role}.service"
        path = Path("/etc/systemd/system") / unit
        protected(path)
        require(
            sha256(_read(path, 65536)).hexdigest()
            == obj(manifest.get("files")).get(f"{node}/{unit}"),
            "BINDING",
            "deployed unit differs from approved plan",
        )
    return inventory, matches[0], name


def key(path: Path, owner: int) -> bytes:
    """Read local key material without exporting it or invoking key-generating native commands."""
    protected(path, owners=(0, owner))
    require(path.stat().st_mode & 0o077 == 0, "CUSTODY", "signing key permissions")
    value = document(_read(path, 8192))
    private = obj(value.get("priv_key"))
    require(private.get("type") == "tendermint/PrivKeyEd25519", "CUSTODY", "Ed25519 key required")
    raw = base64.b64decode(text(private.get("value"), limit=128), validate=True)
    require(len(raw) == 64, "CUSTODY", "native private key length")
    public = Ed25519PrivateKey.from_private_bytes(raw[:32]).public_key().public_bytes_raw()
    require(raw[32:] == public, "CUSTODY", "native private/public key mismatch")
    if path.name == "priv_validator_key.json":
        require(
            "pub_key" in value and "address" in value, "CUSTODY", "validator public fields missing"
        )
    if "pub_key" in value:
        declared = obj(value["pub_key"])
        require(
            declared.get("type") == "tendermint/PubKeyEd25519"
            and declared.get("value") == base64.b64encode(public).decode()
            and value.get("address") == sha256(public).digest()[:20].hex().upper(),
            "CUSTODY",
            "native validator identity mismatch",
        )
    return public


def identities(directory: Path, node: Object, name: str) -> str:
    owner = validator_uid(name)
    home = Path("/var/lib/checkedflow") / name / "validator"
    config = Configuration.decode(
        _read(directory / text(node["name"]) / "operational.json", 4194304)
    )
    public = key(home / "config/priv_validator_key.json", owner)
    require(
        public.hex() == dict(config.validators)[text(node["organization"])],
        "BINDING",
        "validator key differs from reviewed public identity",
    )
    peer = key(home / "config/node_key.json", owner)
    require(sha256(peer).digest()[:20].hex() == node["node_id"], "BINDING", "P2P key differs")
    path = home / "data/priv_validator_state.json"
    protected(path, owners=(0, owner))
    raw = _read(path, 16384)
    state = document(raw)
    require(
        re.fullmatch(r"0|[1-9][0-9]{0,18}", text(state.get("height"), limit=19)) is not None
        and type(state.get("round")) is int
        and int(str(state["round"])) >= 0
        and type(state.get("step")) is int
        and int(str(state["step"])) in range(4),
        "CUSTODY",
        "existing native signing state required",
    )
    return sha256(raw).hexdigest()


def empty_groups(name: str) -> bool:
    root = Path("/sys/fs/cgroup")
    require((root / "cgroup.controllers").is_file(), "PLATFORM", "cgroup v2 inspection required")
    for role in ("abci", "validator"):
        path = root / "system.slice" / f"checkedflow-{name}-{role}.service" / "cgroup.events"
        if path.exists():
            values = dict(line.split() for line in _read(path, 4096).decode("ascii").splitlines())
            if values.get("populated") != "0":
                return False
    return True


def request(arguments: list[str]) -> None:
    try:
        command(arguments)
    except (Failure, OSError, ValueError):
        raise Failure(
            "OUTCOME_UNKNOWN", "native service request result is unknown; inspect status"
        ) from None


def operate(directory: Path, expected: str, wheel: Path | None, node: str, action: str) -> Object:
    require(action in {"status", "start", "stop"}, "ACTION", "supported lifecycle action required")
    _, entry, name = bound(directory, expected, node)
    observed = service_status(name)
    result: Object = {
        "version": "checkedflow/deployment-service/v1",
        "action": action,
        "node": node,
        "plan_sha256": expected,
        "services": observed,
        "release_authority": False,
        "consensus": {
            "status": "NOT_CHECKED",
            "reason": "service state does not prove quorum or application acceptance",
        },
    }
    if action == "status":
        return result | {"status": "OBSERVED", "request_issued": False}
    if action == "start":
        if wheel is None:
            raise Failure("ARGUMENT", "approved wheel required for start")
        inspect(directory, expected, wheel, node)
        identities(directory, entry, name)
        request(
            ["/usr/bin/systemctl", "--no-block", "start", f"checkedflow-{name}-validator.service"]
        )
        return result | {"status": "START_REQUESTED", "request_issued": True}
    # Stop is available when installed artifact verification fails, but never against foreign units.
    request(
        [
            "/usr/bin/systemctl",
            "--no-block",
            "stop",
            f"checkedflow-{name}-validator.service",
            f"checkedflow-{name}-abci.service",
        ]
    )
    try:
        return confirm_stop(result, directory, entry, name)
    except (Failure, OSError, ValueError) as error:
        if isinstance(error, Failure) and error.code == "OUTCOME_UNKNOWN":
            raise
        raise Failure(
            "OUTCOME_UNKNOWN", "stop requested but preservation was not confirmed"
        ) from None


def confirm_stop(result: Object, directory: Path, entry: Object, name: str) -> Object:
    deadline = time.monotonic() + 90
    while True:
        observed = service_status(name)
        if all(
            obj(value)["ActiveState"] == "inactive" and obj(value)["MainPID"] == "0"
            for value in observed.values()
        ) and empty_groups(name):
            return result | {
                "services": observed,
                "status": "STOPPED",
                "request_issued": True,
                "signing_state_sha256": identities(directory, entry, name),
            }
        require(time.monotonic() < deadline, "OUTCOME_UNKNOWN", "service stop was not confirmed")
        time.sleep(0.25)
