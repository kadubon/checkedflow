"""Owned-container recovery failure windows; no candidate execution in source tests."""

import sqlite3
import subprocess
from copy import deepcopy

import pytest

from checkedflow.core.values import Failure
from checkedflow.sandbox_recovery import LABEL, Docker, Recovery
from checkedflow.wire import dumps

BOOT = "12345678-1234-1234-1234-123456789012"
NEXT_BOOT = "12345678-1234-1234-1234-123456789013"
CID = "a" * 64


class Engine:
    def __init__(self):
        self.records = {}
        self.removed = []
        self.daemon = "fixture-daemon"
        self.offline = False
        self.sticky = False
        self.lost = False

    def identity(self):
        if self.offline:
            raise Failure("CLEANUP_UNKNOWN", "offline")
        return self.daemon

    def inspect(self, identity):
        self.identity()
        return next(
            (
                deepcopy(r)
                for key, r in self.records.items()
                if key == identity or r["Name"] == "/" + identity
            ),
            None,
        )

    def remove(self, identity):
        self.identity()
        self.removed.append(identity)
        if not self.sticky:
            del self.records[identity]
        if self.lost:
            raise Failure("CLEANUP_UNKNOWN", "lost deletion reply")

    def create(self, name, token):
        self.records[CID] = {
            "Id": CID,
            "Name": "/" + name,
            "Config": {"Labels": {LABEL: token}},
            "HostConfig": {"Runtime": "runsc"},
        }

    def managed(self):
        return tuple(self.records)


def fixture(path):
    engine, clock = Engine(), [1000000000]
    recovery = Recovery(path, engine, BOOT, clock=lambda: clock[0])
    return engine, clock, recovery


def count(recovery):
    with sqlite3.connect(recovery.path) as db:
        return db.execute("SELECT COUNT(*) FROM containers").fetchone()[0]


def test_stopped_creation_must_be_owned_bound_and_current_before_start(tmp_path):
    engine, clock, recovery = fixture(tmp_path)
    with pytest.raises(Failure, match="not fresh"):
        recovery.reserve(2)
    recovery.sweep(advertise=True)
    with pytest.raises(Failure, match="LIMIT"):
        recovery.reserve(True)
    name, token = recovery.reserve(2)
    with pytest.raises(Failure, match="created container differs"):
        recovery.bind(name, CID)
    engine.create(name, token)
    recovery.bind(name, CID)
    recovery.sweep(advertise=True)
    assert not engine.removed
    assert recovery.cleanup(name) and recovery.cleanup(name)
    assert engine.removed == [CID] and count(recovery) == 0
    with pytest.raises(Failure, match="retired"):
        recovery.bind(name, CID)
    clock[0] += 4000000000
    with pytest.raises(Failure, match="not fresh"):
        recovery.reserve(1)


def test_lost_create_reply_is_not_absence_and_late_creation_is_reaped(tmp_path):
    engine, clock, recovery = fixture(tmp_path)
    recovery.sweep(advertise=True)
    name, token = recovery.reserve(1)
    clock[0] += 1000000000
    recovery.sweep(advertise=True)
    assert count(recovery) == 1 and not recovery.cleanup(name)
    engine.create(name, token)
    recovered = Recovery(tmp_path, engine, BOOT, clock=lambda: clock[0])
    recovered.sweep()
    assert engine.removed == [CID] and count(recovered) == 0


def test_expired_or_previous_boot_intent_cannot_bind(tmp_path):
    engine, clock, recovery = fixture(tmp_path)
    recovery.sweep(advertise=True)
    name, token = recovery.reserve(1)
    engine.create(name, token)
    clock[0] += 1000000000
    with pytest.raises(Failure, match="deadline"):
        recovery.bind(name, CID)
    rebooted = Recovery(tmp_path, engine, NEXT_BOOT, clock=lambda: 1)
    with pytest.raises(Failure, match="not fresh"):
        rebooted.reserve(1)
    rebooted.sweep()
    assert engine.removed == [CID]


@pytest.mark.parametrize("field", ["name", "token", "runtime", "id", "invalid_id"])
def test_reaper_never_removes_mismatched_ownership(tmp_path, field):
    engine, clock, recovery = fixture(tmp_path)
    recovery.sweep(advertise=True)
    name, token = recovery.reserve(1)
    engine.create(name, token)
    recovery.bind(name, CID)
    record = engine.records[CID]
    if field == "name":
        record["Name"] = "/foreign"
    elif field == "token":
        record["Config"]["Labels"][LABEL] = "foreign"
    elif field == "runtime":
        record["HostConfig"]["Runtime"] = "runc"
    else:
        record["Id"] = "b" * 64 if field == "id" else "invalid"
    clock[0] += 1000000000
    with pytest.raises(Failure, match="BINDING"):
        recovery.sweep(advertise=True)
    assert not engine.removed and count(recovery) == 1


def test_lost_removal_reply_keeps_immutable_identity_for_restart(tmp_path):
    engine, clock, recovery = fixture(tmp_path)
    recovery.sweep(advertise=True)
    name, token = recovery.reserve(1)
    engine.create(name, token)
    # Discover an ID even when the worker died before receiving the create reply.
    engine.lost = True
    clock[0] += 1000000000
    with pytest.raises(Failure, match="lost deletion"):
        recovery.sweep(advertise=True)
    assert count(recovery) == 1
    restarted = Recovery(tmp_path, engine, BOOT, clock=lambda: clock[0])
    restarted.sweep()
    assert count(restarted) == 0 and engine.removed == [CID]


def test_unavailable_or_changed_daemon_never_proves_cleanup(tmp_path):
    engine, _, recovery = fixture(tmp_path)
    recovery.sweep(advertise=True)
    name, token = recovery.reserve(1)
    engine.create(name, token)
    recovery.bind(name, CID)
    engine.sticky = True
    with pytest.raises(Failure, match="remains"):
        recovery.cleanup(name)
    engine.offline = True
    with pytest.raises(Failure, match="offline"):
        recovery.cleanup(name)
    assert count(recovery) == 1
    engine.offline, engine.daemon = False, "other"
    with pytest.raises(Failure, match="daemon changed"):
        recovery.sweep(advertise=True)
    with pytest.raises(Failure, match="daemon changed"):
        Recovery(tmp_path, engine, BOOT)


def test_unresolved_capacity_is_bounded_and_future_heartbeat_denies(tmp_path):
    _, clock, recovery = fixture(tmp_path)
    recovery.sweep(advertise=True)
    clock[0] -= 1
    with pytest.raises(Failure, match="not fresh"):
        recovery.reserve(1)
    clock[0] += 1
    for _ in range(32):
        recovery.reserve(1)
    with pytest.raises(Failure, match="capacity"):
        recovery.reserve(1)
    assert count(recovery) == 32


def test_finite_recovery_does_not_advertise_a_continuing_service(tmp_path):
    _, _, recovery = fixture(tmp_path)
    recovery.sweep()
    with pytest.raises(Failure, match="not fresh"):
        recovery.ready()
    recovery.sweep(advertise=True)
    recovery.ready()


def test_missing_journal_cannot_advertise_readiness_over_existing_managed_work(tmp_path):
    engine, _, recovery = fixture(tmp_path)
    engine.create("checkedflow-" + "a" * 32, "unrecorded")
    with pytest.raises(Failure, match="unjournaled"):
        recovery.sweep(advertise=True)
    with pytest.raises(Failure, match="not fresh"):
        recovery.ready()
    assert not engine.removed


def test_slow_inspection_cannot_extend_creation_deadline(tmp_path):
    engine, clock, recovery = fixture(tmp_path)
    recovery.sweep(advertise=True)
    name, token = recovery.reserve(1)
    engine.create(name, token)
    original = engine.inspect

    def slow(identity):
        result = original(identity)
        clock[0] += 1000000000
        return result

    engine.inspect = slow
    with pytest.raises(Failure, match="inspection exceeded"):
        recovery.bind(name, CID)
    assert count(recovery) == 1


def test_two_recovery_callers_do_not_delete_a_replacement_or_repeat_removal(tmp_path, monkeypatch):
    from contextlib import contextmanager

    engine, clock, recovery = fixture(tmp_path)
    recovery.sweep(advertise=True)
    name, token = recovery.reserve(1)
    engine.create(name, token)
    rival = Recovery(tmp_path, engine, BOOT, clock=lambda: clock[0])
    original = recovery._db

    class Connection:
        def __init__(self, db):
            self.db = db

        def execute(self, *args):
            return self.db.execute(*args)

        def commit(self):
            self.db.commit()
            assert rival.cleanup(name)

    @contextmanager
    def interleaved():
        with original() as db:
            yield Connection(db)

    monkeypatch.setattr(recovery, "_db", interleaved)
    assert recovery.cleanup(name)
    assert engine.removed == [CID] and count(recovery) == 0


def test_cli_and_boot_binding_use_only_linux_host_identity(tmp_path, monkeypatch):
    import sys

    import checkedflow.sandbox_recovery as adapter

    monkeypatch.setattr(adapter.platform, "system", lambda: "Windows")
    with pytest.raises(Failure, match="Linux recovery"):
        adapter.boot_id()
    with monkeypatch.context() as patch:
        patch.setattr(adapter.platform, "system", lambda: "Linux")
        patch.setattr(adapter.Path, "read_text", lambda _: BOOT)
        assert adapter.boot_id() == BOOT
    engine = Engine()
    monkeypatch.setattr(adapter, "Docker", lambda _: engine)
    monkeypatch.setattr(adapter, "boot_id", lambda: BOOT)
    monkeypatch.setattr(sys, "argv", ["recovery", str(tmp_path), "--once"])
    adapter.main()
    recovery = Recovery(tmp_path, engine, BOOT)
    with pytest.raises(Failure, match="not fresh"):
        recovery.ready()
    monkeypatch.setattr(sys, "argv", ["recovery", str(tmp_path)])

    def interrupt(seconds):
        assert seconds == 0.25
        raise KeyboardInterrupt

    monkeypatch.setattr(adapter.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        adapter.main()
    recovery.ready()


def test_docker_adapter_distinguishes_successful_absence_from_transport_failure(monkeypatch):
    import checkedflow.sandbox_recovery as adapter

    monkeypatch.setattr(adapter.shutil, "which", lambda _: "/trusted/docker")
    replies, calls = [], []

    def run(args, **kwargs):
        calls.append(args)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return subprocess.CompletedProcess(args, *reply)

    monkeypatch.setattr(adapter.subprocess, "run", run)
    docker = Docker()
    replies.append((0, b"daemon\n", b""))
    assert docker.identity() == "daemon"
    replies.append((0, b"", b""))
    assert docker.inspect(CID) is None
    replies.extend([(0, (CID + "\n").encode(), b""), (0, dumps({"Id": CID}), b"")])
    assert docker.inspect("checkedflow-" + "b" * 32) == {"Id": CID}
    replies.append((0, b"removed", b""))
    docker.remove(CID)
    replies.append((0, (CID + "\n").encode(), b""))
    assert docker.managed() == (CID,)
    replies.append((0, b"", b""))
    assert docker.managed() == ()
    for raw, code in [(b"invalid", "BINDING"), ((CID + "\n").encode() * 33, "LIMIT")]:
        replies.append((0, raw, b""))
        with pytest.raises(Failure, match=code):
            docker.managed()
    assert all(args[1] == "--host=unix:///var/run/docker.sock" for args in calls)
    for reply in [
        (1, b"", b"secret error"),
        subprocess.TimeoutExpired("docker", 10),
        OSError("secret error"),
    ]:
        replies.append(reply)
        with pytest.raises(Failure, match="CLEANUP_UNKNOWN") as error:
            docker.inspect(CID)
        assert "secret error" not in str(error.value)
    replies.append((0, b"x" * 1048577, b""))
    with pytest.raises(Failure, match="LIMIT"):
        docker.identity()
    for raw in [b"", b"x" * 129]:
        replies.append((0, raw, b""))
        with pytest.raises(Failure, match="BINDING"):
            docker.identity()
    for raw in [b"invalid", (CID + "\n" + CID).encode()]:
        replies.append((0, raw, b""))
        with pytest.raises(Failure, match="BINDING"):
            docker.inspect(CID)
    for identity in [".*", "foreign", "a" * 12]:
        with pytest.raises(Failure, match="BINDING"):
            docker.inspect(identity)
        with pytest.raises(Failure, match="BINDING"):
            docker.remove(identity)
    monkeypatch.setattr(adapter.shutil, "which", lambda _: None)
    with pytest.raises(Failure, match="SANDBOX_UNAVAILABLE"):
        Docker()
