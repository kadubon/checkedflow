"""Application-only recovery with independent trust inputs and real SQLite replay."""

import json
import subprocess
import sys
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
from io import BytesIO

import pytest
from test_operational_storage import storage

from checkedflow.core.values import Failure
from checkedflow.operational_backup import decode_checkpoint, export_history, restore_history
from checkedflow.operational_identity import sign_command
from checkedflow.operational_runtime import Runtime
from checkedflow.wire import dumps


def backup(tmp_path):
    store, runtime, command, keys = storage(tmp_path)
    first = store.commit_block(1, [b"bad"], previous_hash=runtime.state_hash)
    second = store.commit_block(
        2,
        [sign_command(command | {"kind": "journal.rollover"}, keys)],
        previous_hash=first.state_hash,
    )
    output = BytesIO()
    checkpoint = export_history(store, output, expected_hash=second.state_hash)
    return store, checkpoint, output.getvalue()


def restore(tmp_path, store, checkpoint, raw, **changes):
    return restore_history(
        BytesIO(raw),
        tmp_path / "restored",
        initial=changes.get("initial", store.initial),
        checkpoint=checkpoint,
        current_height=changes.get("current_height", checkpoint.height),
    )


def test_restoration_replays_rejections_and_regenerates_authenticated_archives(tmp_path):
    store, checkpoint, raw = backup(tmp_path)
    assert decode_checkpoint(dumps(checkpoint.record())) == checkpoint
    result = restore(tmp_path, store, checkpoint, raw)
    assert result.load() == store.load()
    assert result.verify_history(expected_hash=checkpoint.state_hash) == store.load()
    assert result.archive(0, expected_root=store.load().journal.archive_root) == store.archive(
        0, expected_root=store.load().journal.archive_root
    )
    assert not (result.path.parent / "pending.sqlite").exists()
    with pytest.raises(FileExistsError):
        restore(tmp_path, store, checkpoint, raw)
    assert result.load() == store.load()


def test_empty_history_and_zero_block_limit(tmp_path):
    store, runtime, _, _ = storage(tmp_path)
    output = BytesIO()
    checkpoint = export_history(store, output, expected_hash=runtime.state_hash, block_limit=0)
    assert restore(tmp_path, store, checkpoint, output.getvalue()).load() == store.initial


def test_portable_vector_and_schema(tmp_path):
    from jsonschema import Draft202012Validator

    from checkedflow.operational_codec import decode

    data = files("checkedflow").joinpath("data")
    vector = json.loads(data.joinpath("application-backup-vector.json").read_text())
    schema = json.loads(data.joinpath("application-checkpoint.schema.json").read_text())
    Draft202012Validator(schema).validate(vector["checkpoint"])
    checkpoint = decode_checkpoint(dumps(vector["checkpoint"]))
    recovered = restore_history(
        BytesIO(vector["jsonl"].encode()),
        tmp_path / "vector",
        initial=decode(dumps(vector["initial"])),
        checkpoint=checkpoint,
        current_height=2,
    )
    output = BytesIO()
    assert export_history(recovered, output, expected_hash=checkpoint.state_hash) == checkpoint
    assert output.getvalue() == vector["jsonl"].encode()


def test_unknown_cost_and_verification_reserve_survive_replay(tmp_path):
    store, runtime, command, keys = storage(tmp_path)
    previous = runtime.state_hash
    for nonce, (kind, payload) in enumerate(
        [
            ("budget.configure", {"budget": 100, "verification_reserve": 40}),
            ("mission.resume", {}),
            ("budget.reserve", {"phase": "execute", "ceiling": 30, "target": "a" * 64}),
            ("budget.settle", {"ticket": "0:3", "outcome": "unknown", "charged": 30}),
        ],
        1,
    ):
        raw = sign_command(
            command
            | {
                "id": f"0:{nonce}",
                "nonce": nonce,
                "kind": kind,
                "payload": {"mission": "m", **payload},
            },
            keys,
        )
        result = store.commit_block(nonce, [raw], previous_hash=previous)
        assert result.outcomes == ("OK",)
        previous = result.state_hash
    output = BytesIO()
    checkpoint = export_history(store, output, expected_hash=previous)
    recovered = restore(tmp_path, store, checkpoint, output.getvalue()).load()
    assert recovered == store.load()
    assert recovered.budget.spent == 30 and recovered.budget.protected_verification == 40
    assert recovered.budget.tickets[0].status == "unknown"


@pytest.mark.parametrize(
    "change", ["chain", "mission", "genesis_hash", "state_hash", "digest", "length", "height"]
)
def test_changed_independent_checkpoint_cannot_activate(tmp_path, change):
    store, checkpoint, raw = backup(tmp_path)
    value = {"chain": "other", "mission": "other", "length": len(raw) + 1, "height": 3}.get(
        change, "f" * 64
    )
    with pytest.raises(Failure):
        restore(tmp_path, store, replace(checkpoint, **{change: value}), raw)
    assert not (tmp_path / "restored/application.sqlite").exists()


def test_rollback_floor_checked_before_destination_creation(tmp_path):
    store, checkpoint, raw = backup(tmp_path)
    with pytest.raises(Failure, match="predates"):
        restore(tmp_path, store, checkpoint, raw, current_height=checkpoint.height + 1)
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize(
    "mode",
    [
        "truncate",
        "trailing",
        "blank",
        "duplicate",
        "gap",
        "predecessor",
        "outcome",
        "state",
        "noncanonical",
        "extra",
        "oversize",
    ],
)
def test_invalid_history_never_activates_even_with_matching_content_digest(tmp_path, mode):
    store, checkpoint, raw = backup(tmp_path)
    lines = raw.splitlines(keepends=True)
    block = json.loads(lines[1])
    if mode == "truncate":
        raw = raw[:-1]
    elif mode == "trailing":
        raw += b"\n"
    elif mode == "blank":
        lines[1] = b"\n"
        raw = b"".join(lines)
    elif mode == "duplicate":
        raw = raw.replace(b'"height":1', b'"height":1,"height":1', 1)
    elif mode == "oversize":
        from checkedflow.operational_backup import MAX_LINE

        lines[1] = b" " * MAX_LINE + b"\n"
        raw = b"".join(lines)
    else:
        if mode == "gap":
            block["height"] = 2
        elif mode == "predecessor":
            block["previous_hash"] = "f" * 64
        elif mode == "outcome":
            block["transactions"][0]["code"] = "OK"
        elif mode == "state":
            block["state_hash"] = "f" * 64
        elif mode == "extra":
            block["unexpected"] = 1
        lines[1] = (json.dumps(block).encode() if mode == "noncanonical" else dumps(block)) + b"\n"
        raw = b"".join(lines)
    changed = replace(checkpoint, digest=sha256(raw).hexdigest(), length=len(raw))
    with pytest.raises(Failure):
        restore(tmp_path, store, changed, raw)
    assert not (tmp_path / "restored/application.sqlite").exists()
    assert store.verify_history(expected_hash=checkpoint.state_hash) == store.load()


def test_export_uses_one_snapshot_during_concurrent_commit(tmp_path):
    store, checkpoint, _ = backup(tmp_path)

    class ConcurrentOutput(BytesIO):
        def write(self, value):
            if b'"height":1' in value:
                store.commit_block(3, [], previous_hash=checkpoint.state_hash)
            return super().write(value)

    output = ConcurrentOutput()
    exported = export_history(store, output, expected_hash=checkpoint.state_hash)
    assert exported == checkpoint
    assert store.load().height == 3
    assert restore(tmp_path, store, exported, output.getvalue()).load().height == 2


@pytest.mark.parametrize("limits", [{"byte_limit": 1}, {"block_limit": 1}])
def test_export_limits_do_not_return_success(tmp_path, limits):
    store, checkpoint, _ = backup(tmp_path)
    with pytest.raises(Failure, match="ceiling"):
        export_history(store, BytesIO(), expected_hash=checkpoint.state_hash, **limits)


def test_short_output_and_wrong_expected_head_fail(tmp_path):
    store, checkpoint, _ = backup(tmp_path)

    class ShortOutput(BytesIO):
        def write(self, value):
            return 0

    with pytest.raises(Failure, match="incomplete"):
        export_history(store, ShortOutput(), expected_hash=checkpoint.state_hash)
    with pytest.raises(Failure, match="trusted checkpoint"):
        export_history(store, BytesIO(), expected_hash="f" * 64)


def test_export_canonicalizes_unsigned_journal_container_without_changing_transactions(tmp_path):
    store, checkpoint, original = backup(tmp_path)
    with store._connect() as db:
        raw = db.execute("SELECT body FROM blocks WHERE height=1").fetchone()[0]
        spaced = json.dumps(json.loads(raw), indent=2).encode()
        db.execute(
            "UPDATE blocks SET body=?,hash=? WHERE height=1", (spaced, sha256(spaced).hexdigest())
        )
    output = BytesIO()
    assert export_history(store, output, expected_hash=checkpoint.state_hash) == checkpoint
    assert output.getvalue() == original
    assert restore(tmp_path, store, checkpoint, output.getvalue()).load() == store.load()


@pytest.mark.parametrize(
    "field,value",
    [("height", True), ("length", 0), ("genesis_hash", "x"), ("chain", ""), ("version", "future")],
)
def test_checkpoint_rejects_invalid_metadata(tmp_path, field, value):
    _, checkpoint, _ = backup(tmp_path)
    with pytest.raises(Failure):
        decode_checkpoint(dumps(checkpoint.record() | {field: value}))
    with pytest.raises(Failure, match="ceiling"):
        decode_checkpoint(b" " * 4097)


def test_failed_restore_preserves_inactive_staging_and_never_overwrites(tmp_path, monkeypatch):
    from checkedflow import operational_backup

    store, checkpoint, raw = backup(tmp_path)

    def fail_sync(_):
        raise OSError("injected persistence failure")

    with monkeypatch.context() as patch:
        patch.setattr(operational_backup.os, "fsync", fail_sync)
        with pytest.raises(OSError, match="injected"):
            restore(tmp_path, store, checkpoint, raw)
    assert (tmp_path / "restored/pending.sqlite").is_file()
    assert not (tmp_path / "restored/application.sqlite").exists()
    with pytest.raises(FileExistsError):
        restore(tmp_path, store, checkpoint, raw)
    assert Runtime(store.load()).state_hash == checkpoint.state_hash


@pytest.mark.parametrize("phase", ["before", "after"])
def test_real_process_exit_on_either_side_of_activation(tmp_path, phase):
    from checkedflow.operational_codec import decode
    from checkedflow.operational_storage import Store

    vector_path = tmp_path / "public-vector.json"
    vector_path.write_bytes(
        files("checkedflow").joinpath("data/application-backup-vector.json").read_bytes()
    )
    script = """
import json, os, sys
from pathlib import Path
from io import BytesIO
from checkedflow import operational_backup as backup
from checkedflow.operational_codec import decode
from checkedflow.wire import dumps
vector = json.loads(Path(sys.argv[1]).read_text())
checkpoint = backup.decode_checkpoint(dumps(vector['checkpoint']))
if sys.argv[3] == 'before':
    backup.os.fsync = lambda descriptor: os._exit(23)
else:
    original = Path.rename
    def crash_after_rename(self, target):
        original(self, target)
        os._exit(23)
    Path.rename = crash_after_rename
backup.restore_history(BytesIO(vector['jsonl'].encode()), Path(sys.argv[2]),
    initial=decode(dumps(vector['initial'])), checkpoint=checkpoint, current_height=2)
"""
    destination = tmp_path / "crashed"
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(vector_path), str(destination), phase],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 23, result.stderr.decode()
    active = destination / "application.sqlite"
    assert active.exists() is (phase == "after")
    if phase == "after":
        vector = json.loads(vector_path.read_text())
        recovered = Store(active, decode(dumps(vector["initial"])))
        assert (
            recovered.verify_history(expected_hash=vector["checkpoint"]["state_hash"]).height == 2
        )
