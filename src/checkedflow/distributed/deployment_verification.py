"""Verify an approved review bundle and local artifact bytes, without touching hosts."""

import os
import re
import zipfile
import zlib
from email.parser import BytesParser
from hashlib import sha256
from io import BytesIO
from itertools import islice
from pathlib import Path
from typing import cast

from checkedflow import __version__
from checkedflow.core.values import Failure, Object, array, fields, obj, require, text
from checkedflow.distributed.deployment import MAX_INPUT, _inventory, render
from checkedflow.wire import document, dumps

MAX_WHEEL = 16_777_216
MAX_BINARY = 268_435_456


def _hash(value: object) -> str:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "BINDING",
        "canonical SHA-256 required",
    )
    return cast(str, value)


def _plain(path: Path, *, directory: bool = False) -> None:
    require(not path.is_symlink() and not path.is_junction(), "PATH", "linked bundle path")
    require(path.is_dir() if directory else path.is_file(), "PATH", "regular bundle path required")


def _read(path: Path, limit: int) -> bytes:
    _plain(path)
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    require(len(raw) <= limit, "LIMIT", "bundle file byte ceiling")
    return raw


def _entries(path: Path, expected: set[str]) -> None:
    _plain(path, directory=True)
    with os.scandir(path) as source:
        names = {entry.name for entry in islice(source, len(expected) + 1)}
    require(names == expected, "BINDING", "unexpected or missing bundle entries")


def _artifact(path: Path, expected: str, limit: int) -> None:
    _plain(path)
    digest, length = sha256(), 0
    with path.open("rb") as source:
        while chunk := source.read(65536):
            length += len(chunk)
            require(length <= limit, "LIMIT", "artifact byte ceiling")
            digest.update(chunk)
    require(length > 0 and digest.hexdigest() == expected, "BINDING", "artifact digest mismatch")


def _wheel(path: Path, expected: str) -> None:
    raw = _read(path, MAX_WHEEL)
    require(sha256(raw).hexdigest() == expected, "BINDING", "wheel digest mismatch")
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            members = archive.infolist()
            require(
                len(members) <= 4096 and len({m.filename for m in members}) == len(members),
                "PACKAGE",
                "wheel member inventory",
            )
            metadata = [
                member for member in members if member.filename.endswith(".dist-info/METADATA")
            ]
            require(
                len(metadata) == 1 and metadata[0].file_size <= 65536,
                "PACKAGE",
                "one bounded wheel metadata record required",
            )
            record = BytesParser().parsebytes(archive.read(metadata[0]))
            require(
                record.get_all("Name") == ["checkedflow"]
                and record.get_all("Version") == [__version__],
                "PACKAGE",
                "wheel project/version differs from this planner",
            )
    except (
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        EOFError,
        UnicodeError,
        zlib.error,
    ):
        raise Failure("PACKAGE", "unreadable wheel metadata") from None


def verify(directory: Path, expected_plan: str, wheel: Path, cometbft: Path) -> Object:
    """Verify current bundle reads; host installation, custody and native checks are separate."""
    expected_plan = _hash(expected_plan)
    _plain(directory, directory=True)
    raw = _read(directory / "plan.json", 131072)
    require(sha256(raw).hexdigest() == expected_plan, "BINDING", "independent plan digest differs")
    manifest = document(raw)
    fields(manifest, "version inventory inputs files status runtime_verified hosts_changed")
    require(manifest["version"] == "checkedflow/deployment-plan/v1", "VERSION", "plan profile")
    require(
        manifest["status"] == "REVIEW_REQUIRED"
        and manifest["runtime_verified"] is False
        and manifest["hosts_changed"] is False,
        "BINDING",
        "not an offline review plan",
    )
    inventory = _inventory(dumps(manifest["inventory"]))
    sources = obj(manifest["inputs"])
    fields(sources, "inventory configuration genesis")
    for source_hash in sources.values():
        _hash(source_hash)
    declared = obj(manifest["files"])
    for file_hash in declared.values():
        _hash(file_hash)
    nodes = [obj(item) for item in array(inventory["nodes"])]
    names = {text(node["name"]) for node in nodes}
    _entries(directory, names | {"plan.json"})
    first = directory / text(nodes[0]["name"])
    _plain(first, directory=True)
    expected = render(
        dumps(inventory),
        _read(first / "operational.json", MAX_INPUT),
        _read(first / "genesis.json", MAX_INPUT),
    )
    expected.pop("plan.json")
    require(set(declared) == set(expected), "BINDING", "generated file inventory differs")
    for name in names:
        _entries(
            directory / name,
            {path.split("/", 1)[1] for path in expected if path.startswith(name + "/")},
        )
    for path, body in expected.items():
        require(
            _read(directory / path, MAX_INPUT) == body
            and sha256(body).hexdigest() == declared[path],
            "BINDING",
            "generated file differs from approved renderer",
        )
    runtime = obj(inventory["runtime"])
    _wheel(wheel, _hash(runtime["wheel_sha256"]))
    _artifact(cometbft, _hash(runtime["cometbft_sha256"]), MAX_BINARY)
    return {
        "version": "checkedflow/deployment-verification/v1",
        "status": "BUNDLE_VERIFIED",
        "plan_sha256": expected_plan,
        "wheel_sha256": runtime["wheel_sha256"],
        "cometbft_sha256": runtime["cometbft_sha256"],
        "files": len(expected) + 1,
        "artifact_hashes_verified": True,
        "host_preflight": "NOT_PERFORMED",
        "hosts_changed": False,
    }
