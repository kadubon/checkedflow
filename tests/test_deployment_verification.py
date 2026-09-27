"""An approved hash is required; bundle checks never claim native host qualification."""

import json
import zipfile
from hashlib import sha256
from importlib.resources import files
from io import BytesIO
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from test_deployment import inputs, plan

from checkedflow import __version__
from checkedflow.cli import main
from checkedflow.core.values import Failure
from checkedflow.distributed import deployment_verification as verification
from checkedflow.distributed.deployment import write_new
from checkedflow.wire import document, dumps


def bundle(
    tmp_path, *, name="checkedflow", version=__version__, wheel_bytes=None, binary=b"fixture"
):
    wheel, comet = tmp_path / "candidate.whl", tmp_path / "candidate-comet"
    if wheel_bytes is None:
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(
                "checkedflow.dist-info/METADATA",
                f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n",
            )
    else:
        wheel.write_bytes(wheel_bytes)
    # This fixture tests byte identity only, not an executable CometBFT or an installable wheel.
    comet.write_bytes(binary)
    values = inputs()
    values[0]["runtime"]["wheel_sha256"] = sha256(wheel.read_bytes()).hexdigest()
    values[0]["runtime"]["cometbft_sha256"] = sha256(comet.read_bytes()).hexdigest()
    directory = tmp_path / "plan"
    approved = write_new(directory, plan(values))
    return directory, approved, wheel, comet


def approve_modified_manifest(directory, change):
    path = directory / "plan.json"
    manifest = document(path.read_bytes())
    change(manifest)
    path.write_bytes(dumps(manifest))
    return sha256(path.read_bytes()).hexdigest()


def test_bundle_and_cli_are_observations_not_execution_authority(tmp_path, capfd):
    directory, approved, wheel, comet = bundle(tmp_path)
    result = verification.verify(directory, approved, wheel, comet)
    schema = json.loads(
        files("checkedflow").joinpath("data/deployment-verification.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(result)
    assert result["status"] == "BUNDLE_VERIFIED" and result["files"] == 21
    assert result["host_preflight"] == "NOT_PERFORMED" and result["hosts_changed"] is False
    assert (
        main(
            [
                "deployment-verify",
                "--directory",
                str(directory),
                "--expected-plan",
                approved,
                "--wheel",
                str(wheel),
                "--cometbft",
                str(comet),
            ]
        )
        == 0
    )
    assert json.loads(capfd.readouterr().out) == result
    comet.write_bytes(b"changed after observation")
    with pytest.raises(Failure, match="artifact digest"):
        verification.verify(directory, approved, wheel, comet)


@pytest.mark.parametrize("expected", ["0" * 64, "A" * 64, "short", None])
def test_independent_approval_cannot_be_inferred_from_manifest(tmp_path, expected):
    directory, _, wheel, comet = bundle(tmp_path)
    with pytest.raises(Failure, match="BINDING"):
        verification.verify(directory, expected, wheel, comet)


@pytest.mark.parametrize(
    "target", ["root-extra", "node-extra", "missing", "changed", "tampered", "foreign-map"]
)
def test_bundle_cannot_include_unreviewed_or_rewritten_files(tmp_path, target):
    directory, approved, wheel, comet = bundle(tmp_path)
    file = directory / "node0" / "config.toml"
    if target == "root-extra":
        (directory / "private.key").write_bytes(b"unapproved")
    elif target == "node-extra":
        (directory / "node0" / "private.key").write_bytes(b"unapproved")
    elif target == "missing":
        file.unlink()
    elif target == "tampered":
        file.write_bytes(b"unsafe = true")
    elif target == "changed":
        file.write_bytes(b"unsafe = true")
        approved = approve_modified_manifest(
            directory,
            lambda m: m["files"].update(
                {"node0/config.toml": sha256(file.read_bytes()).hexdigest()}
            ),
        )
    else:
        approved = approve_modified_manifest(
            directory, lambda m: m["files"].update({"../unrelated-secret": "0" * 64})
        )
    with pytest.raises(Failure, match="BINDING"):
        verification.verify(directory, approved, wheel, comet)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "unknown"),
        ("status", "APPLIED"),
        ("hosts_changed", True),
        ("runtime_verified", True),
        ("inputs", {"inventory": "x"}),
        ("files", {"node0/config.toml": "invalid"}),
    ],
)
def test_invalid_reapproved_manifests_still_fail_closed(tmp_path, field, value):
    directory, _, wheel, comet = bundle(tmp_path)
    approved = approve_modified_manifest(directory, lambda m: m.update({field: value}))
    with pytest.raises(Failure):
        verification.verify(directory, approved, wheel, comet)


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "foreign"},
        {"version": "999.0.0"},
        {"name": "checkedflow\nName: checkedflow"},
        {"wheel_bytes": b"not a wheel"},
        {"binary": b""},
    ],
)
def test_hash_agreement_does_not_replace_package_identity(tmp_path, changes):
    args = bundle(tmp_path, **changes)
    with pytest.raises(Failure):
        verification.verify(*args)


def test_changed_wheel_and_bounded_reads(tmp_path, monkeypatch):
    directory, approved, wheel, comet = bundle(tmp_path)
    wheel.write_bytes(wheel.read_bytes() + b"changed")
    with pytest.raises(Failure, match="wheel digest"):
        verification.verify(directory, approved, wheel, comet)
    monkeypatch.setattr(verification, "MAX_WHEEL", 1)
    with pytest.raises(Failure, match="LIMIT"):
        verification.verify(directory, approved, wheel, comet)
    monkeypatch.setattr(verification, "MAX_WHEEL", 16777216)
    wheel.write_bytes(wheel.read_bytes()[:-7])
    monkeypatch.setattr(verification, "MAX_BINARY", 1)
    with pytest.raises(Failure, match="LIMIT"):
        verification.verify(directory, approved, wheel, comet)


@pytest.mark.parametrize("predicate", ["is_symlink", "is_junction"])
def test_linked_node_directories_are_rejected_before_read(tmp_path, monkeypatch, predicate):
    directory, approved, wheel, comet = bundle(tmp_path)
    original = getattr(Path, predicate)
    monkeypatch.setattr(Path, predicate, lambda self: self == directory / "node0" or original(self))
    with pytest.raises(Failure, match="linked"):
        verification.verify(directory, approved, wheel, comet)


@pytest.mark.parametrize("kind", ["missing", "oversized", "corrupt-compression"])
def test_bounded_package_metadata_rejects_malformed_archives(tmp_path, kind):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if kind != "missing":
            body = b"x" * 65537 if kind == "oversized" else b"Name: checkedflow"
            archive.writestr("checkedflow.dist-info/METADATA", body)
    raw = buffer.getvalue()
    if kind == "corrupt-compression":
        size = len("checkedflow.dist-info/METADATA")
        raw = raw[: 30 + size] + b"\x07" + raw[31 + size :]
    with pytest.raises(Failure, match="PACKAGE"):
        verification.verify(*bundle(tmp_path, wheel_bytes=raw))
