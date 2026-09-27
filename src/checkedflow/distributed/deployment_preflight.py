"""Read-only Linux host checks against an independently approved deployment bundle."""

import os
import stat
import subprocess  # nosec B404 - fixed inspection commands after operator executable checks
import sys
import sysconfig
import zipfile
from hashlib import sha256
from itertools import islice
from pathlib import Path, PurePosixPath

from checkedflow.core.values import Failure, Object, array, obj, require, text
from checkedflow.distributed.deployment_verification import MAX_WHEEL, _read, verify
from checkedflow.wire import document

COMET_MODULE = (
    "github.com/cometbft/cometbft",
    "v0.40.0",
    "h1:0+zc7FlcnonFfMwzhyDCd5vK/fHZqz0RfRyTCxZsFPU=",
)


def protected(path: Path, *, executable: bool = False, owners: tuple[int, ...] = (0,)) -> Path:
    """Require operator custody of the supplied path and its resolved target ancestry."""
    absolute = path.absolute()
    resolved = path.resolve(strict=True)
    for item in {absolute, *absolute.parents, resolved, *resolved.parents}:
        info = item.lstat()
        require(info.st_uid in owners, "CUSTODY", "host path has an unexpected owner")
        # Root-owned symlinks are permitted for virtual-environment interpreter links.
        if not stat.S_ISLNK(info.st_mode):
            require(info.st_mode & 0o022 == 0, "CUSTODY", "host path is externally writable")
            if executable and stat.S_ISDIR(info.st_mode):
                require(
                    info.st_mode & 0o001 != 0,
                    "PERMISSION",
                    "service accounts cannot traverse runtime ancestry",
                )
    if executable:
        require(
            resolved.is_file() and resolved.stat().st_mode & 0o005 == 0o005,
            "PERMISSION",
            "runtime must be readable and executable by service accounts",
        )
    return resolved


def installed_members(wheel: Path) -> int:
    """Compare current package files, reject extra source and retain bounded inspection."""
    import checkedflow

    package = Path(str(checkedflow.__file__)).parent
    require(
        package == Path(sysconfig.get_path("purelib")) / "checkedflow",
        "PACKAGE",
        "installed package required; source checkout is not deployment",
    )
    protected(package)
    expected: set[str] = set()
    total = 0
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            if not member.filename.startswith("checkedflow/") or member.is_dir():
                continue
            path = PurePosixPath(member.filename)
            require(
                str(path) == member.filename and ".." not in path.parts and "\\" not in str(path),
                "PACKAGE",
                "canonical wheel member required",
            )
            total += member.file_size
            require(
                0 <= member.file_size <= 4_194_304 and total <= 2 * MAX_WHEEL,
                "LIMIT",
                "installed package byte ceiling",
            )
            relative = str(path.relative_to("checkedflow"))
            target = package / relative
            protected(target)
            require(
                _read(target, 4_194_304) == archive.read(member),
                "PACKAGE",
                "installed bytes differ",
            )
            expected.add(relative)
    require(bool(expected), "PACKAGE", "wheel contains no package files")
    entries = list(islice(package.rglob("*"), 4097))
    require(len(entries) <= 4096, "LIMIT", "installed file inventory ceiling")
    for entry in entries:
        protected(entry)
        require(not entry.is_symlink(), "PATH", "linked package entry")
        if entry.is_file():
            entry_relative = entry.relative_to(package)
            cached = "__pycache__" in entry_relative.parts and entry.suffix == ".pyc"
            require(
                cached or entry_relative.as_posix() in expected,
                "PACKAGE",
                "unapproved package file",
            )
    return len(expected)


def command(arguments: list[str]) -> str:
    """Execute only protected operator runtimes with a fixed deadline and clean environment."""
    protected(Path(arguments[0]), executable=True)
    try:
        result = subprocess.run(  # nosec B603: fixed arrays, protected operator executables
            arguments,
            check=True,
            capture_output=True,
            timeout=15,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            cwd="/",
        )
    except (OSError, subprocess.SubprocessError):
        raise Failure("NATIVE", "native inspection failed or timed out") from None
    require(len(result.stdout) + len(result.stderr) <= 65536, "LIMIT", "native response ceiling")
    return result.stdout.decode("utf-8", errors="strict").strip()


def host() -> None:
    """Reject unsupported hosts before accessing deployment paths."""
    require(sys.platform == "linux", "PLATFORM", "Linux host required")
    require(
        getattr(os, "geteuid", lambda: -1)() == 0 and sys.flags.isolated == 1,
        "CUSTODY",
        "root isolated Python required",
    )


def validator_uid(name: str) -> int:
    import pwd

    try:
        identity = int(getattr(pwd, "getpwnam")(f"cf-{name}-val").pw_uid)  # noqa: B009 - Linux API
    except KeyError:
        raise Failure("CUSTODY", "validator service account missing") from None
    require(identity != 0, "CUSTODY", "non-root validator account required")
    return identity


def inspect(directory: Path, expected_plan: str, wheel: Path, node: str) -> Object:
    """Inspect one already provisioned host; never install, start, stop or alter signer state."""
    host()

    protected(directory)
    protected(wheel)
    manifest = document(_read(directory / "plan.json", 131072))
    inventory = obj(manifest.get("inventory"))
    runtime = obj(inventory.get("runtime"))
    binary = Path(text(runtime.get("cometbft")))
    # Verification authenticates the complete bundle before interpreting host actions or identities.
    verified = verify(directory, expected_plan, wheel, binary)
    names = {text(obj(item)["name"]) for item in array(inventory["nodes"])}
    require(node in names, "BINDING", "node absent from reviewed inventory")
    interpreter = protected(Path(text(runtime["python"])), executable=True)
    require(
        Path(sys.executable).absolute() == Path(text(runtime["python"])).absolute()
        and interpreter == Path(sys.executable).resolve(),
        "PACKAGE",
        "reviewed interpreter required",
    )
    protected(binary, executable=True)
    count = installed_members(wheel)
    name = text(inventory["name"])
    owner = validator_uid(name)
    etc = Path("/etc/checkedflow") / name
    home = Path("/var/lib/checkedflow") / name
    declared = obj(manifest["files"])
    for filename in (
        "config.toml",
        "operational.json",
        "genesis.json",
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
        protected(target, owners=(0, owner) if filename == "config.toml" else (0,))
        require(
            sha256(_read(target, 4_194_304)).hexdigest() == declared[f"{node}/{filename}"],
            "BINDING",
            "installed configuration differs",
        )
    build = command(["/usr/bin/go", "version", "-m", str(binary)])
    lines = [line.split() for line in build.splitlines()]
    require(
        [tuple(line[1:]) for line in lines if line and line[0] == "mod"] == [COMET_MODULE]
        and not any(line and line[0] == "=>" for line in lines),
        "VERSION",
        "pinned native CometBFT module required without replacements",
    )
    version = document(command([str(binary), "version", "--verbose"]).encode())
    require(
        version.get("abci") == "2.0.0"
        and version.get("block_protocol") == 11
        and version.get("p2p_protocol") == 8,
        "VERSION",
        "native CometBFT protocol versions differ",
    )
    services: Object = {}
    for role in ("abci", "validator"):
        unit = f"checkedflow-{name}-{role}.service"
        values = command(
            [
                "/usr/bin/systemctl",
                "show",
                unit,
                "--property=LoadState,ActiveState,SubState,MainPID,FragmentPath,DropInPaths,NeedDaemonReload,User,Group",
            ]
        )
        properties = dict(line.split("=", 1) for line in values.splitlines())
        require(properties.get("LoadState") == "loaded", "SERVICE", "service unit not loaded")
        account = f"cf-{name}-{'app' if role == 'abci' else 'val'}"
        require(
            properties.get("FragmentPath") == f"/etc/systemd/system/{unit}"
            and properties.get("DropInPaths") == ""
            and properties.get("NeedDaemonReload") == "no"
            and properties.get("User") == properties.get("Group") == account,
            "SERVICE",
            "loaded service differs from reviewed unit",
        )
        services[role] = {
            key: properties[key] for key in ("LoadState", "ActiveState", "SubState", "MainPID")
        }
    return {
        "version": "checkedflow/deployment-preflight/v1",
        "status": "HOST_FILES_VERIFIED",
        "plan_sha256": verified["plan_sha256"],
        "wheel_sha256": verified["wheel_sha256"],
        "node": node,
        "installed_package_members": count,
        "cometbft_version": text(version.get("cometbft"), limit=80),
        "cometbft_module_version": COMET_MODULE[1],
        "services": services,
        "hosts_changed": False,
        "startup_authorized": False,
        "remaining": [
            "network confinement",
            "validator custody",
            "candidate isolation",
            "live quorum and recovery",
        ],
    }
