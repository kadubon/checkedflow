"""Callback tokens stay out of persisted plaintext and transport responses."""

import asyncio
import json
import sqlite3

import pytest
from a2a.server.context import ServerCallContext
from a2a.types import a2a_pb2 as pb
from agent_backend import Backend
from google.protobuf.json_format import MessageToDict, ParseDict

from checkedflow.agents.a2a import Handler
from checkedflow.agents.journal import Journal
from checkedflow.agents.push import Push
from checkedflow.agents.secrets import MAX_CONFIG_BYTES, Keyring, public_configuration
from checkedflow.core.values import Failure
from checkedflow.wire import document, dumps

VALUE = {
    "id": "notify",
    "taskId": "t",
    "url": "https://callbacks.example/",
    "token": "fixture-notification-secret",
    "authentication": {"scheme": "Bearer", "credentials": "fixture-auth-secret"},
}


def test_sealing_nonce_binding_and_integrity():
    ring = Keyring.ephemeral()
    first, second = ring.seal(VALUE, b"scope"), ring.seal(VALUE, b"scope")
    assert first != second and ring.open(first, b"scope") == VALUE
    assert b"fixture-auth-secret" not in first and b"fixture-notification-secret" not in first
    for binding in (b"other-client", b"other-mission", b"other-task"):
        with pytest.raises(Failure, match="SECRET_INTEGRITY"):
            ring.open(first, binding)
    record = document(first[4:])
    record["data"] = ("00" if record["data"][:2] != "00" else "01") + record["data"][2:]
    with pytest.raises(Failure, match="SECRET_INTEGRITY"):
        ring.open(b"CFP1" + dumps(record), b"scope")
    with pytest.raises(Failure, match="SECRET_INTEGRITY"):
        Keyring.ephemeral().open(first, b"scope")


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b"CFP1{}",
        b"CFP1not-json",
        b'CFP1{"key":"memory","nonce":"00","data":"00"}',
        b'CFP1{"key":"memory","nonce":"zz","data":"00"}',
    ],
)
def test_malformed_ciphertext_is_not_plaintext_fallback(raw):
    with pytest.raises(Failure):
        Keyring.ephemeral().open(raw, b"scope")


def test_ciphertext_and_plaintext_bounds():
    ring = Keyring.ephemeral()
    with pytest.raises(Failure, match="LIMIT"):
        ring.seal({"token": "a" * (MAX_CONFIG_BYTES + 1)}, b"scope")
    with pytest.raises(Failure, match="LIMIT"):
        ring.open(b"CFP1" + b"a" * (2 * MAX_CONFIG_BYTES + 257), b"scope")


@pytest.mark.parametrize(
    "authentication",
    [
        {"scheme": "Basic", "credentials": "x"},
        {"scheme": "Digest"},
        {"schemes": ["Bearer"], "credentials": "x"},
        {"credentials": "x"},
        {"scheme": []},
        {"scheme": True},
    ],
)
def test_callback_authentication_cannot_be_reinterpreted(authentication):
    with pytest.raises(Failure, match="PUSH_AUTH"):
        Push(("callbacks.example",)).validate(VALUE | {"authentication": authentication})
    assert (
        Push(("callbacks.example",)).validate(
            VALUE | {"authentication": {"scheme": "bEaReR", "credentials": "x"}}
        )
        == VALUE["url"]
    )


def test_key_file_load_and_errors(tmp_path):
    path = tmp_path / "keys"
    with pytest.raises(Failure, match="SECRET_KEY"):
        Keyring.load(path)
    path.write_text(json.dumps({"active": "test", "keys": {"test": "42" * 32}}))
    ring = Keyring.load(path)
    assert ring.open(ring.seal(VALUE, b"x"), b"x") == VALUE
    for value in (
        {"active": "wrong", "keys": {"test": "42" * 32}},
        {"active": "test", "keys": {"test": "zz"}},
        {"active": "test", "keys": {"test": "42"}},
        {},
    ):
        path.write_text(json.dumps(value))
        with pytest.raises(Failure, match="SECRET_KEY"):
            Keyring.load(path)
    path.write_bytes(b"a" * 4097)
    with pytest.raises(Failure, match="SECRET_KEY"):
        Keyring.load(path)


def test_persistent_journal_requires_key_and_preserves_ciphertext(h, tmp_path):
    h.task(start=False)
    gateway = Backend(h).gateway()
    path = tmp_path / "journal.sqlite"
    ring = Keyring.ephemeral()
    journal = Journal(gateway, path)
    with pytest.raises(Failure, match="SECRET_KEY"):
        journal.put_config("t", "notify", VALUE)
    journal.close()
    journal = Journal(gateway, path, keyring=ring)
    journal.refresh()
    journal.put_config("t", "notify", VALUE)
    encrypted = journal.db.execute("SELECT value FROM notifications").fetchone()[0]
    journal.close()
    for file in tmp_path.iterdir():
        raw = file.read_bytes()
        assert b"fixture-auth-secret" not in raw and b"fixture-notification-secret" not in raw
    with pytest.raises(Failure, match="SECRET_KEY"):
        Journal(gateway, path)
    with pytest.raises(Failure, match="SECRET_INTEGRITY"):
        Journal(gateway, path, keyring=Keyring.ephemeral())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM notifications").fetchone()[0] == encrypted
    resumed = Journal(gateway, path, keyring=ring)
    assert resumed.configurations("t") == [VALUE]
    resumed.delivery("t", "notify", VALUE, resumed.pending()[0][3], False)
    assert resumed.pending()[0][4] == 1
    resumed.close()


def test_old_plaintext_is_not_silently_migrated(h, tmp_path):
    gateway = Backend(h).gateway()
    path = tmp_path / "legacy.sqlite"
    journal = Journal(gateway, path)
    with journal.db:
        journal.db.execute(
            "INSERT INTO notifications(task,id,value) VALUES(?,?,?)", ("t", "notify", dumps(VALUE))
        )
    journal.close()
    with pytest.raises(Failure, match="SECRET_FORMAT"):
        Journal(gateway, path, keyring=Keyring.ephemeral())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM notifications").fetchone()[0] == dumps(VALUE)


def test_rows_cannot_be_swapped_between_configurations_or_journals(h, tmp_path):
    gateway = Backend(h).gateway()
    ring = Keyring.ephemeral()
    first = Journal(gateway, tmp_path / "one", keyring=ring)
    second = Journal(gateway, tmp_path / "two", keyring=ring)
    try:
        first.put_config("t", "notify", VALUE)
        raw = first.db.execute("SELECT value FROM notifications").fetchone()[0]
        for task, identity in (("other", "notify"), ("t", "other")):
            with pytest.raises(Failure, match="SECRET_INTEGRITY"):
                first._open(task, identity, raw)
        with pytest.raises(Failure, match="SECRET_INTEGRITY"):
            second._open("t", "notify", raw)
    finally:
        first.close()
        second.close()


def test_rewrap_is_atomic_and_keeps_delivery_state(h, tmp_path):
    h.task(start=False)
    gateway = Backend(h).gateway()
    path = tmp_path / "journal"
    old = Keyring.ephemeral()
    new = Keyring("next", {"next": b"N" * 32})
    journal = Journal(gateway, path, keyring=old)
    journal.refresh()
    journal.put_config("t", "notify", VALUE)
    journal.delivery("t", "notify", VALUE, journal.pending()[0][3], False)
    revision = journal.configuration_revision("t")
    assert journal.rewrap(new) == 1
    assert journal.configuration_revision("t") != revision
    assert journal.pending()[0][2] == VALUE and journal.pending()[0][4] == 1
    journal.close()
    resumed = Journal(gateway, path, keyring=new)
    assert resumed.configurations("t") == [VALUE]
    resumed.put_config("t", "second", VALUE | {"id": "second"})
    before = resumed.db.execute("SELECT value FROM notifications ORDER BY id").fetchall()

    class Fails(Keyring):
        calls = 0

        def seal(self, value, binding):
            self.calls += 1
            if self.calls == 2:
                raise OSError("fixture failure")
            return super().seal(value, binding)

    with pytest.raises(OSError):
        resumed.rewrap(Fails("next", {"next": b"X" * 32}))
    assert resumed.db.execute("SELECT value FROM notifications ORDER BY id").fetchall() == before
    assert resumed.configurations("t")[0]["token"] == VALUE["token"]
    resumed.close()


def test_create_get_list_responses_are_redacted_but_dispatch_has_credentials(h):
    async def scenario():
        h.task(start=False)
        handler = Handler(Backend(h).gateway(), push=Push(("callbacks.example",)))
        try:
            context = ServerCallContext()
            created = await handler.on_create_task_push_notification_config(
                ParseDict(VALUE, pb.TaskPushNotificationConfig()), context
            )
            fetched = await handler.on_get_task_push_notification_config(
                pb.GetTaskPushNotificationConfigRequest(task_id="t", id="notify"), context
            )
            listed = await handler.on_list_task_push_notification_configs(
                pb.ListTaskPushNotificationConfigsRequest(task_id="t"), context
            )
            for response in (created, fetched, listed):
                raw = json.dumps(MessageToDict(response))
                assert "fixture-auth-secret" not in raw and "fixture-notification-secret" not in raw
            assert handler.journal.pending()[0][2] == VALUE
            assert public_configuration({"id": "none"}) == {"id": "none"}
            handler.journal.delete_config("t", "notify")
            handler.journal.delivery("t", "notify", VALUE, "fingerprint", True)
        finally:
            handler.journal.close()

    asyncio.run(scenario())


def test_process_exit_during_rewrap_keeps_original_key_and_rows(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    from test_operational_gateway import Node

    child = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from test_operational_gateway import Node
from checkedflow.agents.journal import Journal
from checkedflow.agents.secrets import Keyring
path = Path(sys.argv[2])
n = Node()
j = Journal(n.gateway(), path, keyring=Keyring('old', {'old': b'O'*32}))
j.put_config(n.task, 'one', {'id':'one', 'token':'fixture-only'})
j.put_config(n.task, 'two', {'id':'two', 'token':'fixture-only'})
class Crash(Keyring):
    calls = 0
    def seal(self, value, binding):
        self.calls += 1
        if self.calls == 2:
            os._exit(39)
        return super().seal(value, binding)
j.rewrap(Crash('new', {'new':b'N'*32}))
"""
    path = tmp_path / "crash.sqlite"
    result = subprocess.run(
        [sys.executable, "-c", child, str(Path(__file__).parent), str(path)],
        timeout=30,
        check=False,
    )
    assert result.returncode == 39
    n = Node()
    resumed = Journal(n.gateway(), path, keyring=Keyring("old", {"old": b"O" * 32}))
    try:
        assert len(resumed.configurations(n.task)) == 2
        assert all(value["token"] == "fixture-only" for value in resumed.configurations(n.task))
    finally:
        resumed.close()


def test_packaged_keyring_contract_matches_loader_boundaries(tmp_path):
    from importlib.resources import files

    from jsonschema import Draft202012Validator

    schema = json.loads(
        files("checkedflow").joinpath("data/callback-keyring.schema.json").read_text()
    )
    validator = Draft202012Validator(schema)
    valid = {"active": "fixture", "keys": {"fixture": "42" * 32}}
    validator.validate(valid)
    path = tmp_path / "keyring.json"
    for value in (
        {"active": "fixture", "keys": {"fixture": "42" * 32 + "\n"}},
        {"active": "fixture\n", "keys": {"fixture\n": "42" * 32}},
        {"active": "x" * 32 + "\n", "keys": {"x" * 32 + "\n": "42" * 32}},
        {"active": "fixture", "keys": {"fixture": "42" * 31}},
        {"active": "fixture", "keys": {"fixture": "42" * 32}, "extra": True},
    ):
        assert not validator.is_valid(value)
        path.write_text(json.dumps(value))
        with pytest.raises(Failure, match="SECRET_KEY"):
            Keyring.load(path)
