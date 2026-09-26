"""Actual SQLite persistence and process-crash tests for the initial control profile."""

import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from test_operational_runtime import runtime_and_command

from checkedflow.core.values import Failure
from checkedflow.operational_codec import archive_bytes, decode, decode_archive, state_bytes
from checkedflow.operational_identity import sign_command
from checkedflow.operational_storage import Store
from checkedflow.wire import dumps, loads


def storage(tmp_path):
    runtime, command, keys = runtime_and_command()
    store = Store(tmp_path / "control.sqlite", runtime.state)
    return store, runtime, command, keys


def test_committed_archive_reopens_and_history_replays(tmp_path):
    store, runtime, command, keys = storage(tmp_path)
    before = runtime.state_hash
    pause = sign_command(command, keys)
    first = store.commit_block(1, [pause], previous_hash=before)
    assert first.outcomes == ("OK",)
    assert store.commit_block(1, [pause], previous_hash=before) == first
    rollover = sign_command(
        command | {"id": "0:checkpoint", "nonce": 2, "kind": "journal.rollover"}, keys
    )
    second = store.commit_block(2, [b"bad", rollover], previous_hash=first.state_hash)
    assert second.outcomes == ("JSON", "OK")
    reopened = Store(store.path, runtime.state)
    state = reopened.load()
    assert state.journal.epoch == 1 and state.height == 2
    assert dict(state.journal.actors)["a"] == 2
    assert reopened.verify_history(expected_hash=second.state_hash) == state
    archived = reopened.archive(0, expected_root=state.journal.archive_root)
    assert len(archived.receipts) == 2
    with pytest.raises(Failure, match="archive commitment"):
        reopened.archive(0, expected_root="f" * 64)
    with pytest.raises(Failure, match="unavailable"):
        reopened.archive(1, expected_root="f" * 64)
    with pytest.raises(Failure, match="trusted checkpoint"):
        reopened.verify_history(expected_hash="f" * 64)


def test_state_and_archive_roll_back_together_on_storage_failure(tmp_path, monkeypatch):
    store, runtime, command, keys = storage(tmp_path)
    checkpoint = sign_command(command | {"kind": "journal.rollover"}, keys)
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute(
            "CREATE TRIGGER fail_head BEFORE UPDATE ON head "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.commit_block(1, [checkpoint], previous_hash=runtime.state_hash)
    assert store.load() == runtime.state
    with closing(sqlite3.connect(store.path)) as db, db:
        assert db.execute("SELECT count(*) FROM blocks").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM epochs").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_head")
    from checkedflow import operational_storage

    def unavailable_archive(_):
        raise Failure("STORAGE", "injected archive encoding failure")

    with monkeypatch.context() as patch:
        patch.setattr(operational_storage, "archive_bytes", unavailable_archive)
        with pytest.raises(Failure, match="encoding failure"):
            store.commit_block(1, [checkpoint], previous_hash=runtime.state_hash)
    assert store.load() == runtime.state
    store.commit_block(1, [checkpoint], previous_hash=runtime.state_hash)
    assert store.load().journal.epoch == 1


@pytest.mark.parametrize("crash_before_commit", [False, True])
def test_real_process_exit_before_or_after_atomic_commit(tmp_path, crash_before_commit):
    store, runtime, command, keys = storage(tmp_path)
    initial_file = tmp_path / "initial.json"
    initial_file.write_bytes(state_bytes(runtime.state))
    checkpoint = sign_command(command | {"kind": "journal.rollover"}, keys)
    script = """
import os, sys
from pathlib import Path
from contextlib import contextmanager
from checkedflow.operational_codec import decode
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import Store
initial = decode(Path(sys.argv[2]).read_bytes())
store = Store(Path(sys.argv[1]), initial)
if sys.argv[4] == "before":
    original = Store._connect
    @contextmanager
    def crash(self):
        with original(self) as db:
            db.execute("PRAGMA cache_size=1")
            yield db
            os._exit(23)
    Store._connect = crash
store.commit_block(1, [bytes.fromhex(sys.argv[3])], previous_hash=Runtime(initial).state_hash)
os._exit(23)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            str(store.path),
            str(initial_file),
            checkpoint.hex(),
            "before" if crash_before_commit else "after",
        ],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 23, result.stderr.decode()
    reopened = Store(store.path, runtime.state)
    assert reopened.load().height == (0 if crash_before_commit else 1)
    result = reopened.commit_block(1, [checkpoint], previous_hash=runtime.state_hash)
    assert result.outcomes == ("OK",)
    assert reopened.load().journal.epoch == 1
    assert reopened.verify_history(expected_hash=result.state_hash) == reopened.load()


def test_conflicting_concurrent_blocks_do_not_double_advance(tmp_path):
    store, runtime, command, keys = storage(tmp_path)
    raws = [
        sign_command(command, keys),
        sign_command(command | {"kind": "mission.resume", "id": "0:resume"}, keys),
    ]

    def commit(raw):
        try:
            store.commit_block(1, [raw], previous_hash=runtime.state_hash)
            return "OK"
        except Failure as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(commit, raws)) == ["CONFLICT", "OK"]
    assert dict(store.load().journal.actors)["a"] == 1
    with pytest.raises(Failure, match="contiguous"):
        store.commit_block(3, [], previous_hash=runtime.state_hash)
    with pytest.raises(Failure, match="stale"):
        store.commit_block(2, [], previous_hash=runtime.state_hash)


def test_codec_rejects_shape_nonce_and_registry_corruption(tmp_path):
    store, runtime, command, keys = storage(tmp_path)
    store.commit_block(1, [sign_command(command, keys)], previous_hash=runtime.state_hash)
    state = store.load()
    assert decode(state_bytes(state)) == state
    for mutate in (
        lambda obj: obj.update(mode="invented"),
        lambda obj: obj["journal"]["actors"].reverse(),
        lambda obj: obj["journal"]["receipts"][0].update(nonce=2),
        lambda obj: obj["journal"]["receipts"][0].update(administrative=False),
        lambda obj: obj["credentials"][0].update(revoked=1),
        lambda obj: obj.update(profile="checkedflow/v1"),
    ):
        value = loads(state_bytes(state))
        mutate(value)
        with pytest.raises(Failure):
            decode(dumps(value))
    with pytest.raises(Failure, match="different genesis"):
        Store(store.path, replace(runtime.state, chain="other"))


def test_archive_or_snapshot_removal_and_corruption_fail_closed(tmp_path):
    store, runtime, command, keys = storage(tmp_path)
    checkpoint = sign_command(command | {"kind": "journal.rollover"}, keys)
    committed = store.commit_block(1, [checkpoint], previous_hash=runtime.state_hash)
    state = store.load()
    with closing(sqlite3.connect(store.path)) as db, db:
        body = db.execute("SELECT body FROM epochs").fetchone()[0]
        db.execute("UPDATE epochs SET body=?", (b"{}",))
    with pytest.raises(Failure):
        store.load()
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("UPDATE epochs SET body=?", (body,))
        db.execute("DELETE FROM epochs")
    with pytest.raises(Failure, match="missing current epoch"):
        store.load()
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("INSERT INTO epochs VALUES (0, ?, ?)", (state.journal.archive_root, body))
        db.execute("UPDATE head SET body=?", (b"{}",))
    with pytest.raises(Failure, match="state digest"):
        store.load()
    assert committed.height == 1


def test_history_tampering_is_detected_even_if_blob_digest_is_recomputed(tmp_path):
    store, runtime, command, keys = storage(tmp_path)
    result = store.commit_block(1, [sign_command(command, keys)], previous_hash=runtime.state_hash)
    with closing(sqlite3.connect(store.path)) as db, db:
        value = loads(db.execute("SELECT body FROM blocks").fetchone()[0])
        value["transactions"][0]["code"] = "SIGNATURE"
        raw = dumps(value)
        db.execute("UPDATE blocks SET body=?, hash=?", (raw, sha256(raw).hexdigest()))
    with pytest.raises(Failure, match="recorded outcome"):
        store.verify_history(expected_hash=result.state_hash)
    with pytest.raises(Failure):
        decode_archive(b"{}", "0" * 64)


def test_limits_empty_blocks_and_forged_immutable_configuration(tmp_path):
    store, runtime, _, _ = storage(tmp_path)
    for transactions in ([b""] * 257, [b"x" * 1048577], [b"x" * 1048576] * 2 + [b"x"]):
        with pytest.raises(Failure, match="ceiling|count"):
            store.commit_block(1, transactions, previous_hash=runtime.state_hash)
    committed = store.commit_block(1, [], previous_hash=runtime.state_hash)
    assert committed.outcomes == () and store.load().height == 1
    forged = replace(store.load(), chain="forged-chain")
    raw = state_bytes(forged)
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("UPDATE head SET body=?, hash=?", (raw, sha256(raw).hexdigest()))
    with pytest.raises(Failure, match="immutable control configuration"):
        store.load()


def test_structural_state_and_archive_schemas(tmp_path):
    store, runtime, command, keys = storage(tmp_path)
    store.commit_block(
        1,
        [sign_command(command | {"kind": "journal.rollover"}, keys)],
        previous_hash=runtime.state_hash,
    )
    state = store.load()
    archive = store.archive(0, expected_root=state.journal.archive_root)
    for name, raw in [
        ("operational-state", state_bytes(state)),
        ("request-archive", archive_bytes(archive)),
    ]:
        schema = loads(files("checkedflow").joinpath(f"data/{name}.schema.json").read_bytes())
        validator = Draft202012Validator(schema)
        value = loads(raw)
        validator.validate(value)
        value["unapproved"] = True
        assert not validator.is_valid(value)


def test_unrelated_database_is_not_migrated_or_put_into_wal(tmp_path):
    runtime, _, _ = runtime_and_command()
    path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE snapshot (value TEXT)")
        before = db.execute("PRAGMA journal_mode").fetchone()[0]
    with pytest.raises(Failure, match="store profile"):
        Store(path, runtime.state)
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == before
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ("snapshot",)
        ]
