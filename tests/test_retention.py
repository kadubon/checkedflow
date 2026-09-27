"""Real catalog transactions, lifetime protection, uncertainty and scoped recovery."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
from io import BytesIO

import pytest
from jsonschema import Draft202012Validator

from checkedflow.artifact_io import Access
from checkedflow.artifacts import LocalStore
from checkedflow.core.artifact import Reference
from checkedflow.core.values import MAX_INT, Failure
from checkedflow.retention import Pin, Plan, RetentionStore, decode_plan
from checkedflow.wire import dumps

ACCESS = Access(
    "owner", frozenset({"mission"}), frozenset({"read", "write", "pin", "maintain", "erase"})
)


def ref(body=b"artifact"):
    return Reference(
        "sha256", sha256(body).hexdigest(), len(body), "text/plain", "evidence", "mission", "a" * 64
    )


def catalog(tmp_path, provider=None, **kwargs):
    provider = provider or LocalStore(tmp_path / "bytes.sqlite")
    return RetentionStore(
        tmp_path / "retention.sqlite",
        provider,
        namespace="operator-a-test",
        scope="mission",
        trusted_floor=kwargs.pop("trusted_floor", 0),
        retention_blocks=2,
        grace_blocks=3,
        **kwargs,
    )


def publish(store, body=b"artifact"):
    store.put(ref(body), BytesIO(body), access=ACCESS)


def test_grace_pins_reopen_and_read_only_plan(tmp_path):
    store = catalog(tmp_path)
    publish(store)
    assert store.get(ref(), access=ACCESS) == b"artifact"
    store.advance(4, access=ACCESS)
    assert not store.plan(access=ACCESS).objects
    store.advance(5, access=ACCESS)
    plan = store.plan(access=ACCESS)
    assert plan.objects == (ref(),)
    assert store.revision(access=ACCESS) == plan.revision
    assert decode_plan(dumps(plan.record())) == plan
    pin = store.pin("operation", (ref(),), category="pending", access=ACCESS)
    assert store.pin("operation", (ref(),), category="pending", access=ACCESS) == pin
    with pytest.raises(Failure, match="STALE_PLAN"):
        store.sweep(plan, access=ACCESS)
    floor = store.revision(access=ACCESS)
    store = catalog(tmp_path, trusted_floor=floor)
    store.advance(100, access=ACCESS)
    assert not store.plan(access=ACCESS).objects  # Pins do not expire on crashes or elapsed time.
    store.release(pin, access=ACCESS)
    assert not store.plan(access=ACCESS).objects
    store.advance(102, access=ACCESS)
    assert not store.plan(access=ACCESS).objects
    store.advance(103, access=ACCESS)
    assert store.sweep(store.plan(access=ACCESS), access=ACCESS)[0].status == "erased"
    with pytest.raises(Failure, match="UNAVAILABLE"):
        store.provider.get(ref(), access=ACCESS)


def test_all_root_categories_and_shared_physical_bindings(tmp_path):
    store = catalog(tmp_path)
    publish(store)
    pins = [
        store.pin(kind, (ref(), replace(ref(), manifest="b" * 64)), category=kind, access=ACCESS)
        for kind in ("pending", "dependency", "effect", "snapshot", "replay")
    ]
    store.advance(1000, access=ACCESS)
    for pin in pins[:-1]:
        store.release(pin, access=ACCESS)
    assert not store.plan(access=ACCESS).objects
    store.release(pins[-1], access=ACCESS)
    store.advance(1003, access=ACCESS)
    assert store.plan(access=ACCESS).objects == (ref(),)


def test_replaced_pin_fence_and_other_principal(tmp_path):
    store = catalog(tmp_path)
    publish(store)
    old = store.pin("operation", (ref(),), category="pending", access=ACCESS)
    with pytest.raises(Failure, match="CONFLICT"):
        store.pin("operation", (ref(),), category="effect", access=ACCESS)
    with pytest.raises(Failure, match="AUTHORITY"):
        store.release(old, access=replace(ACCESS, principal="other"))
    store.release(old, access=ACCESS)
    new = store.pin("operation", (ref(),), category="pending", access=ACCESS)
    assert new.sequence > old.sequence
    with pytest.raises(Failure, match="STALE_PIN"):
        store.release(old, access=ACCESS)
    assert not store.plan(access=ACCESS).objects


def test_tombstone_precedes_effect_and_survives_provider_resurrection(tmp_path, monkeypatch):
    store = catalog(tmp_path)
    publish(store)
    store.advance(5, access=ACCESS)
    erase = store.provider.erase

    def observe_tombstone(ref, *, access):
        with closing(sqlite3.connect(store.path)) as db:
            assert db.execute("SELECT status FROM retention_objects").fetchone()[0] == "tombstoned"
        erase(ref, access=access)

    monkeypatch.setattr(store.provider, "erase", observe_tombstone)
    assert store.sweep(store.plan(access=ACCESS), access=ACCESS)[0].status == "erased"
    store.provider.put(
        ref(), BytesIO(b"artifact"), access=ACCESS
    )  # Simulated old byte-store restore.
    floor = store.revision(access=ACCESS)
    store = catalog(tmp_path, trusted_floor=floor)
    for operation in (
        lambda: store.get(ref(), access=ACCESS),
        lambda: publish(store),
        lambda: store.pin("new", (ref(),), category="pending", access=ACCESS),
    ):
        with pytest.raises(Failure, match="RETIRED_ARTIFACT"):
            operation()
    assert (
        store.reconcile_erasure(ref(), access=ACCESS).status == "erased"
    )  # Historical confirmation.


@pytest.mark.parametrize("after_effect", [False, True])
def test_uncertain_erasure_keeps_tombstone_and_capacity(tmp_path, monkeypatch, after_effect):
    store = catalog(tmp_path, object_limit=1)
    publish(store)
    store.advance(5, access=ACCESS)
    erase = store.provider.erase

    def interrupted(ref, *, access):
        if after_effect:
            erase(ref, access=access)
        raise OSError("fixture connection failed")

    monkeypatch.setattr(store.provider, "erase", interrupted)
    assert store.sweep(store.plan(access=ACCESS), access=ACCESS)[0].status == "unknown"
    with pytest.raises(Failure, match="CAPACITY"):
        publish(store, b"another")
    monkeypatch.setattr(store.provider, "erase", erase)
    assert store.reconcile_erasure(ref(), access=ACCESS).status == "erased"
    publish(store, b"another")
    assert store.get(ref(b"another"), access=ACCESS) == b"another"


def test_old_catalog_below_independent_floor_cannot_restore(tmp_path):
    store = catalog(tmp_path)
    publish(store)
    backup = tmp_path / "old.sqlite"
    with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    store.advance(5, access=ACCESS)
    store.sweep(store.plan(access=ACCESS), access=ACCESS)
    floor = store.revision(access=ACCESS)
    with pytest.raises(Failure, match="RESTORE"):
        RetentionStore(
            backup,
            store.provider,
            namespace=store.namespace,
            scope=store.scope,
            trusted_floor=floor,
            retention_blocks=2,
            grace_blocks=3,
        )
    with pytest.raises(Failure, match="RESTORE"):
        catalog(tmp_path)  # Reopening with an invented genesis watermark is rejected.
    with pytest.raises(Failure, match="RESTORE"):
        catalog(tmp_path / "lost", trusted_floor=floor)


def test_authorization_precedes_catalog_stream_and_provider_access(tmp_path, monkeypatch):
    store = catalog(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("unauthorized I/O")

    class Unreadable:
        read = forbidden

    monkeypatch.setattr(store, "_connect", forbidden)
    denied = replace(ACCESS, scopes=frozenset({"other"}))
    pin = Pin("mission", "pin", 1, "owner")
    plan = Plan(store.namespace, store.scope, 1, (ref(),))
    for operation in (
        lambda: store.put(ref(), Unreadable(), access=denied),
        lambda: store.get(ref(), access=denied),
        lambda: store.advance(1, access=denied),
        lambda: store.revision(access=denied),
        lambda: store.pin("pin", (ref(),), category="pending", access=denied),
        lambda: store.release(pin, access=denied),
        lambda: store.plan(access=denied),
        lambda: store.sweep(plan, access=denied),
        lambda: store.reconcile_erasure(ref(), access=denied),
    ):
        with pytest.raises(Failure, match="AUTHORITY"):
            operation()


def test_forged_current_plan_cannot_erase_protected_or_young_objects(tmp_path):
    store = catalog(tmp_path)
    publish(store)
    plan = Plan(store.namespace, store.scope, store.revision(access=ACCESS), (ref(),))
    with pytest.raises(Failure, match="RETAINED"):
        store.sweep(plan, access=ACCESS)
    store.advance(5, access=ACCESS)
    store.pin("protected", (ref(),), category="snapshot", access=ACCESS)
    plan = replace(plan, revision=store.revision(access=ACCESS))
    with pytest.raises(Failure, match="RETAINED"):
        store.sweep(plan, access=ACCESS)
    with pytest.raises(Failure, match="RETAINED"):
        store.reconcile_erasure(ref(), access=ACCESS)


def test_concurrent_puts_cannot_overdraw_and_refresh_retention(tmp_path):
    store = catalog(tmp_path, object_limit=1)

    def put(index):
        try:
            publish(store, str(index).encode())
            return index
        except Failure as failure:
            assert failure.code == "CAPACITY"
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(put, range(8)))
    selected = next(item for item in results if item is not None)
    assert sum(item is not None for item in results) == 1
    store.advance(100, access=ACCESS)
    publish(store, str(selected).encode())  # A verified duplicate starts a new minimum retention.
    assert not store.plan(access=ACCESS).objects
    store.advance(105, access=ACCESS)
    assert len(store.plan(access=ACCESS).objects) == 1


def test_limits_shapes_corrupt_identity_and_byte_quota(tmp_path):
    store = catalog(tmp_path, byte_limit=1)
    with pytest.raises(Failure, match="CAPACITY"):
        publish(store)
    with pytest.raises(Failure, match="CONFLICT"):
        catalog(tmp_path, trusted_floor=1, byte_limit=2)
    with pytest.raises(Failure, match="SCOPE"):
        store.get(replace(ref(), scope="other"), access=ACCESS)
    with pytest.raises(Failure, match="UNAVAILABLE"):
        store.get(ref(), access=ACCESS)
    for kwargs in ({"category": "unknown"}, {"category": "pending"}):
        with pytest.raises(Failure):
            store.pin("empty", (), access=ACCESS, **kwargs)
    with pytest.raises(Failure):
        store.advance(-1, access=ACCESS)
    with pytest.raises(Failure):
        store.plan(access=ACCESS, limit=129)
    with pytest.raises(Failure, match="LIMIT"):
        store.sweep(store.plan(access=ACCESS), access=ACCESS)
    with pytest.raises(Failure, match="SHAPE"):
        Pin("mission", "x", True, "owner")
    with pytest.raises(Failure, match="SHAPE"):
        Plan("namespace", "mission", True, ())
    for value in (
        b"x" * 131073,
        b"{}",
        dumps(store.plan(access=ACCESS).record() | {"version": "future"}),
    ):
        with pytest.raises(Failure):
            decode_plan(value)
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("UPDATE retention_identity SET revision=?", (MAX_INT,))
    with pytest.raises(Failure, match="CAPACITY"):
        store.advance(1, access=ACCESS)
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("DELETE FROM retention_identity")
    with pytest.raises(Failure, match="STORAGE"):
        catalog(tmp_path, trusted_floor=1, byte_limit=1)


def test_unrelated_database_is_not_repurposed(tmp_path):
    with closing(sqlite3.connect(tmp_path / "retention.sqlite")) as db:
        db.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(Failure, match="VERSION"):
        catalog(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE retention_identity SET profile='future'",
        "ALTER TABLE retention_identity RENAME COLUMN profile TO future_profile",
    ],
)
def test_unknown_catalog_version_or_layout_rejects(tmp_path, mutation):
    store = catalog(tmp_path)
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute(mutation)
    with pytest.raises(Failure, match="VERSION"):
        catalog(tmp_path, trusted_floor=1)


def test_process_crash_after_tombstone_never_reopens_admission(tmp_path):
    store = catalog(tmp_path)
    publish(store)
    store.advance(5, access=ACCESS)
    floor = store.revision(access=ACCESS)
    program = """
import os, sys
from pathlib import Path
from checkedflow.artifact_io import Access
from checkedflow.artifacts import LocalStore
from checkedflow.retention import RetentionStore
root = Path(sys.argv[1])
provider = LocalStore(root / 'bytes.sqlite')
def crash(ref, *, access):
    os._exit(97)
provider.erase = crash
access = Access('owner', frozenset({'mission'}), frozenset({'read','maintain','erase'}))
store = RetentionStore(root / 'retention.sqlite', provider, namespace='operator-a-test',
    scope='mission', trusted_floor=int(sys.argv[2]), retention_blocks=2, grace_blocks=3)
store.sweep(store.plan(access=access), access=access)
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path), str(floor)],
        capture_output=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 97, result.stderr.decode()
    recovered = catalog(tmp_path, trusted_floor=floor)
    with pytest.raises(Failure, match="RETIRED_ARTIFACT"):
        recovered.get(ref(), access=ACCESS)
    assert recovered.provider.get(ref(), access=ACCESS) == b"artifact"
    assert recovered.reconcile_erasure(ref(), access=ACCESS).status == "erased"


def test_plan_schema_and_portable_vectors():
    schema = json.loads(
        files("checkedflow").joinpath("data/retention-plan.schema.json").read_text()
    )
    vectors = json.loads(files("checkedflow").joinpath("data/retention-vectors.json").read_text())
    validator = Draft202012Validator(schema)
    for record in vectors["valid"]:
        validator.validate(record)
        assert decode_plan(dumps(record)).record() == record
    for record in vectors["invalid"]:
        assert list(validator.iter_errors(record))
        with pytest.raises(Failure):
            decode_plan(dumps(record))
