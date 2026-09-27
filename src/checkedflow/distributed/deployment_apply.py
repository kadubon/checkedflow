"""Install approved public node files without overwriting state or starting services."""

import os
import secrets
import stat
from hashlib import sha256
from pathlib import Path

from checkedflow.core.values import Failure, Object, array, obj, require, text
from checkedflow.distributed.deployment_lock import exclusive
from checkedflow.distributed.deployment_preflight import (
    account_ids,
    command,
    host,
    inspect,
    protected,
    runtime_check,
)
from checkedflow.distributed.deployment_service import empty_groups, identities
from checkedflow.distributed.deployment_verification import _read, verify
from checkedflow.wire import document


def quiescent(name: str) -> None:
    """Do not update a running or queued service, including one not installed yet."""
    for role in ("abci", "validator"):
        value = command(
            [
                "/usr/bin/systemctl",
                "show",
                f"checkedflow-{name}-{role}.service",
                "--property=ActiveState,MainPID,Job",
            ]
        )
        properties = dict(line.split("=", 1) for line in value.splitlines())
        require(
            properties.get("ActiveState") == "inactive"
            and properties.get("MainPID") == "0"
            and properties.get("Job") in {"", "0"},
            "BUSY",
            "inactive node services without pending jobs required",
        )
    require(empty_groups(name), "BUSY", "service control groups must be empty")


def install_new(target: Path, body: bytes) -> bool:
    """Use an anchored directory descriptor; link new bytes without replacement."""
    nofollow = int(getattr(os, "O_NOFOLLOW", 0))
    directory = int(getattr(os, "O_DIRECTORY", 0))
    nonblock = int(getattr(os, "O_NONBLOCK", 0))
    require(
        nofollow != 0 and directory != 0 and nonblock != 0,
        "PLATFORM",
        "Linux directory descriptor flags required",
    )
    parent = os.open(target.parent, os.O_RDONLY | nofollow | directory)
    try:
        try:
            descriptor = os.open(target.name, os.O_RDONLY | nofollow | nonblock, dir_fd=parent)
        except FileNotFoundError:
            descriptor = None
        if descriptor is not None:
            with os.fdopen(descriptor, "rb") as existing:
                require(
                    stat.S_ISREG(os.fstat(existing.fileno()).st_mode),
                    "PATH",
                    "regular destination required",
                )
                require(
                    existing.read(4194305) == body, "CONFLICT", "existing deployment file differs"
                )
            return False
        staging = ".checkedflow-" + secrets.token_hex(12)
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as output:
            chmod = getattr(os, "fchmod", None)
            if not callable(chmod):
                raise Failure("PLATFORM", "descriptor chmod required")
            chmod(output.fileno(), 0o644)
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.link(staging, target.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.unlink(staging, dir_fd=parent)
        os.fsync(parent)
        return True
    finally:
        os.close(parent)


def apply(directory: Path, expected: str, wheel: Path, node: str) -> Object:
    host()
    protected(directory)
    protected(wheel)
    manifest = document(_read(directory / "plan.json", 131072))
    inventory = obj(manifest.get("inventory"))
    runtime = obj(inventory.get("runtime"))
    verified = verify(directory, expected, wheel, Path(text(runtime.get("cometbft"))))
    matches = [obj(row) for row in array(inventory["nodes"]) if obj(row)["name"] == node]
    require(len(matches) == 1, "BINDING", "node absent from reviewed plan")
    name = text(inventory["name"])
    runtime_check(runtime, wheel)
    app_uid, validator = account_ids(name)
    etc = Path("/etc/checkedflow") / name
    home = Path("/var/lib/checkedflow") / name
    protected(etc)
    for folder, owner in (("application", app_uid), ("validator", validator)):
        path = home / folder
        protected(path, owners=(0, owner))
        require(
            path.is_dir() and path.stat().st_uid == owner,
            "CUSTODY",
            "provisioned service home required",
        )
        filesystem = command(["/usr/bin/stat", "--file-system", "--format=%T", str(path)])
        require(
            filesystem in {"ext2/ext3", "xfs", "btrfs"},
            "STORAGE",
            "supported local filesystem required",
        )
    prepared: list[tuple[Path, bytes, int]] = []
    for filename in (
        "config.toml",
        "genesis.json",
        "operational.json",
        f"checkedflow-{name}-abci.service",
        f"checkedflow-{name}-validator.service",
    ):
        target = (
            home / "validator/config/config.toml"
            if filename == "config.toml"
            else Path("/etc/systemd/system") / filename
            if filename.endswith(".service")
            else etc / filename
        )
        owner = validator if filename == "config.toml" else 0
        protected(target.parent, owners=(0, owner))
        body = _read(directory / node / filename, 4194304)
        require(
            sha256(body).hexdigest() == obj(manifest["files"])[f"{node}/{filename}"],
            "BINDING",
            "review bytes changed",
        )
        prepared.append((target, body, owner))
    with (
        exclusive(etc / "deployment.lock", 0, create=True),
        exclusive(home / "validator/custody.lock", validator),
    ):
        quiescent(name)
        identities(directory, matches[0], name)
        # Check all conflicts before writing any configuration or unit file.
        for target, body, owner in prepared:
            if target.exists() or target.is_symlink():
                protected(target, owners=(0, owner))
                require(
                    _read(target, 4194304) == body, "CONFLICT", "existing deployment file differs"
                )
        created = sum(install_new(target, body) for target, body, _ in prepared)
        try:
            command(["/usr/bin/systemctl", "daemon-reload"])
            quiescent(name)
            observed = inspect(directory, expected, wheel, node)
        except (Failure, OSError, ValueError):
            raise Failure(
                "OUTCOME_UNKNOWN", "files retained; manager reload or readback not confirmed"
            ) from None
    return {
        "version": "checkedflow/deployment-apply/v1",
        "status": "FILES_APPLIED",
        "plan_sha256": verified["plan_sha256"],
        "node": node,
        "files_created": created,
        "files_verified": 5,
        "startup_authorized": False,
        "services_started": False,
        "preflight": observed,
    }
