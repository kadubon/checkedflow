"""Offline CLI creates new operator outputs, never a live validator."""

import json

import pytest
from test_operational_backup import backup

from checkedflow.cli import main
from checkedflow.operational_codec import state_bytes


def test_cli_export_inspect_restore_and_refuse_overwrite(tmp_path, capfdbinary):
    store, checkpoint, raw = backup(tmp_path)
    genesis = tmp_path / "initial.json"
    genesis.write_bytes(state_bytes(store.initial))
    destination = tmp_path / "export"
    command = [
        "application-backup",
        "export",
        "--genesis",
        str(genesis),
        "--database",
        str(store.path),
        "--expected-hash",
        checkpoint.state_hash,
        "--destination",
        str(destination),
    ]
    assert main(command) == 0
    assert json.loads(capfdbinary.readouterr().out)["independent_custody_required"] is True
    assert (destination / "history.jsonl").read_bytes() == raw
    metadata = str(destination / "checkpoint.json")
    assert main(["application-backup", "inspect-checkpoint", "--checkpoint", metadata]) == 0
    parsed = json.loads(capfdbinary.readouterr().out)
    assert parsed["authenticated"] is False and parsed["backup_verified"] is False
    assert (
        main(
            [
                "application-backup",
                "restore",
                "--genesis",
                str(genesis),
                "--source",
                str(destination / "history.jsonl"),
                "--checkpoint",
                metadata,
                "--current-height",
                "2",
                "--destination",
                str(tmp_path / "restored"),
            ]
        )
        == 0
    )
    result = json.loads(capfdbinary.readouterr().out)
    assert result["state_hash"] == checkpoint.state_hash and result["validator_started"] is False
    assert main(command) == 2
    assert json.loads(capfdbinary.readouterr().err)["error"] == "IO"


@pytest.mark.parametrize(
    "mode", ["oversized", "missing", "wrong_hash", "byte_limit", "corrupt_sqlite"]
)
def test_failed_cli_export_has_no_completion_checkpoint(tmp_path, capfdbinary, mode):
    store, checkpoint, _ = backup(tmp_path)
    genesis = tmp_path / "genesis.json"
    genesis.write_bytes(state_bytes(store.initial))
    if mode == "oversized":
        genesis.write_bytes(b" " * (4194304 + 1))
    if mode == "corrupt_sqlite":
        store.path.write_bytes(b"not a sqlite database")
    destination = tmp_path / "failed"
    command = [
        "application-backup",
        "export",
        "--genesis",
        str(genesis),
        "--database",
        str(store.path if mode != "missing" else tmp_path / "absent.sqlite"),
        "--expected-hash",
        checkpoint.state_hash if mode != "wrong_hash" else "f" * 64,
        "--destination",
        str(destination),
    ]
    if mode == "byte_limit":
        command += ["--byte-limit", "1"]
    assert main(command) == 2
    assert "error" in json.loads(capfdbinary.readouterr().err)
    assert not (destination / "checkpoint.json").exists()
    assert not (tmp_path / "absent.sqlite").exists()
