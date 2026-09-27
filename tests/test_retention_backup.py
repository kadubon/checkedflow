"""Actual catalog snapshots and fail-closed staged restoration; no candidate execution."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
from io import BytesIO

import pytest
from jsonschema import Draft202012Validator

from checkedflow.artifact_io import Access
from checkedflow.artifacts import LocalStore
from checkedflow.core.artifact import Reference
from checkedflow.core.values import Failure
from checkedflow.retention import RetentionStore
from checkedflow.retention_backup import (
    MAX_BYTES,
    MAX_LINE,
    MAX_RECORDS,
    Checkpoint,
    decode_checkpoint,
    export_catalog,
    restore_catalog,
)
from checkedflow.wire import document, dumps

ACCESS = Access(
    "owner",
    frozenset({"mission"}),
    frozenset({"read", "write", "pin", "maintain", "erase", "backup", "restore"}),
)


def reference(body):
    return Reference(
        "sha256", sha256(body).hexdigest(), len(body), "text/plain", "evidence", "mission", "a" * 64
    )


def setup(tmp_path):
    store = RetentionStore(
        tmp_path / "source.sqlite",
        LocalStore(tmp_path / "bytes.sqlite"),
        namespace="operator",
        scope="mission",
        trusted_floor=0,
        retention_blocks=2,
        grace_blocks=3,
    )
    for body in (b"live", b"retired"):
        store.put(reference(body), BytesIO(body), access=ACCESS)
    pin = store.pin("work", (reference(b"live"),), category="pending", access=ACCESS)
    store.advance(5, access=ACCESS)
    store.sweep(store.plan(access=ACCESS), access=ACCESS)
    return store, pin


def backup(store, **kwargs):
    output = BytesIO()
    checkpoint = export_catalog(store, output, access=ACCESS, **kwargs)
    return output.getvalue(), checkpoint


def restore(tmp_path, store, body, checkpoint, **kwargs):
    return restore_catalog(
        BytesIO(body),
        tmp_path / "restored",
        store.provider,
        checkpoint=checkpoint,
        current_revision=checkpoint.revision,
        namespace="operator",
        scope="mission",
        access=ACCESS,
        **kwargs,
    )


def reanchor(body, checkpoint):
    # Deliberately authenticate malformed input to exercise semantic checks separately from trust.
    return replace(
        checkpoint, digest=sha256(body).hexdigest(), length=len(body), records=body.count(b"\n")
    )


def test_snapshot_preserves_roots_accounting_tombstones_and_fences(tmp_path):
    store, pin = setup(tmp_path)
    body, checkpoint = backup(store)
    assert decode_checkpoint(dumps(checkpoint.record())) == checkpoint
    assert checkpoint.records == 4
    assert checkpoint.digest == sha256(body).hexdigest()
    restored = restore(tmp_path, store, body, checkpoint)
    assert restored.revision(access=ACCESS) == checkpoint.revision
    assert restored.get(reference(b"live"), access=ACCESS) == b"live"
    assert not restored.plan(access=ACCESS).objects
    # Restoring provider bytes does not undo a catalog tombstone.
    store.provider.put(reference(b"retired"), BytesIO(b"retired"), access=ACCESS)
    with pytest.raises(Failure, match="RETIRED_ARTIFACT"):
        restored.get(reference(b"retired"), access=ACCESS)
    restored.release(pin, access=ACCESS)
    restored.advance(8, access=ACCESS)
    assert restored.plan(access=ACCESS).objects == (reference(b"live"),)
    assert backup(restored)[1].revision > checkpoint.revision


def test_unknown_erasure_survives_without_provider_io_during_restore(tmp_path, monkeypatch):
    store, pin = setup(tmp_path)
    store.release(pin, access=ACCESS)
    store.advance(8, access=ACCESS)
    original = store.provider.erase

    def interrupted(*args, **kwargs):
        raise OSError("fixture outage")

    monkeypatch.setattr(store.provider, "erase", interrupted)
    assert store.sweep(store.plan(access=ACCESS), access=ACCESS)[0].status == "unknown"
    body, checkpoint = backup(store)
    restored = restore(tmp_path, store, body, checkpoint)
    with pytest.raises(Failure, match="RETIRED_ARTIFACT"):
        restored.get(reference(b"live"), access=ACCESS)
    monkeypatch.setattr(store.provider, "erase", original)
    assert restored.reconcile_erasure(reference(b"live"), access=ACCESS).status == "erased"


def test_concurrent_source_write_is_not_mixed_into_read_snapshot(tmp_path):
    store, pin = setup(tmp_path)
    before = store.revision(access=ACCESS)

    class ConcurrentOutput(BytesIO):
        def write(self, raw):
            if self.tell() == 0:
                store.release(pin, access=ACCESS)
                store.put(reference(b"later"), BytesIO(b"later"), access=ACCESS)
            return super().write(raw)

    output = ConcurrentOutput()
    checkpoint = export_catalog(store, output, access=ACCESS)
    assert checkpoint.revision == before < store.revision(access=ACCESS)
    restored = restore(tmp_path, store, output.getvalue(), checkpoint)
    assert not restored.plan(access=ACCESS).objects
    with pytest.raises(Failure, match="UNAVAILABLE"):
        restored.get(reference(b"later"), access=ACCESS)


@pytest.mark.parametrize("change", ["digest", "short", "extra", "records"])
def test_corrupt_or_partial_snapshot_never_activates(tmp_path, change):
    store, _ = setup(tmp_path)
    body, checkpoint = backup(store)
    if change == "digest":
        checkpoint = replace(checkpoint, digest="0" * 64)
    elif change == "short":
        body = body[:-1]
    elif change == "extra":
        body += b"\n"
    else:
        checkpoint = replace(checkpoint, records=checkpoint.records + 1)
    with pytest.raises(Failure):
        restore(tmp_path, store, body, checkpoint)
    assert not (tmp_path / "restored").exists()
    assert store.get(reference(b"live"), access=ACCESS) == b"live"


def test_current_floor_and_namespace_precede_input_and_filesystem_access(tmp_path):
    store, _ = setup(tmp_path)
    body, checkpoint = backup(store)

    class Forbidden:
        def readline(self, *args):
            raise AssertionError("unauthorized input read")

    defaults = dict(
        checkpoint=checkpoint,
        current_revision=checkpoint.revision,
        namespace="operator",
        scope="mission",
        access=ACCESS,
    )
    for override, code in [
        ({"current_revision": checkpoint.revision + 1}, "RESTORE"),
        ({"namespace": "other"}, "SCOPE"),
        ({"scope": "other"}, "AUTHORITY"),
        ({"access": replace(ACCESS, permissions=frozenset())}, "AUTHORITY"),
        ({"current_revision": 0}, "SHAPE"),
    ]:
        with pytest.raises(Failure, match=code):
            restore_catalog(
                Forbidden(), tmp_path / "restored", store.provider, **(defaults | override)
            )
    assert not (tmp_path / "restored").exists()
    assert body


def test_existing_destination_is_never_overwritten(tmp_path):
    store, _ = setup(tmp_path)
    body, checkpoint = backup(store)
    destination = tmp_path / "restored"
    destination.mkdir()
    marker = destination / "catalog.sqlite"
    marker.write_bytes(b"existing operator data")
    with pytest.raises(FileExistsError):
        restore(tmp_path, store, body, checkpoint)
    assert marker.read_bytes() == b"existing operator data"


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "scope",
        "revision",
        "height",
        "counter",
        "quota",
        "object-order",
        "object-after-pin",
        "object-scope",
        "status",
        "until",
        "pin-sequence",
        "pin-category",
        "empty-root",
        "missing-root",
        "retired-root",
        "duplicate-root",
        "duplicate-pin",
        "kind",
    ],
)
def test_authenticated_but_invalid_records_reject(tmp_path, mutation):
    store, _ = setup(tmp_path)
    body, checkpoint = backup(store)
    rows = [document(raw) for raw in body.splitlines()]
    header, first, second, pin = rows
    if mutation == "version":
        header["version"] = "unknown"
    elif mutation in {"scope", "revision", "height"}:
        header[mutation] = "other" if mutation == "scope" else 999
    elif mutation == "counter":
        header["bytes"] = 0
    elif mutation == "quota":
        header["byte_limit"] = 1
        header["bytes"] = 1
    elif mutation == "object-order":
        rows[1:3] = [second, first]
    elif mutation == "object-after-pin":
        rows[2:4] = [pin, second]
    elif mutation == "object-scope":
        first["reference"]["scope"] = "other"
    elif mutation == "status":
        first["status"] = "pending"
    elif mutation == "until":
        first["until"] = 100
    elif mutation == "pin-sequence":
        pin["sequence"] = checkpoint.revision + 1
    elif mutation == "pin-category":
        pin["category"] = "arbitrary"
    elif mutation == "empty-root":
        pin["digests"] = []
    elif mutation == "missing-root":
        pin["digests"] = ["f" * 64]
    elif mutation == "retired-root":
        pin["digests"] = [reference(b"retired").digest]
    elif mutation == "duplicate-root":
        pin["digests"] *= 2
    elif mutation == "duplicate-pin":
        rows.append(pin)
    else:
        pin["type"] = "unexpected"
    body = b"".join(dumps(row) + b"\n" for row in rows)
    with pytest.raises(Failure):
        restore(tmp_path, store, body, reanchor(body, checkpoint))
    assert not (tmp_path / "restored").exists()


def test_limits_short_write_and_authority(tmp_path, monkeypatch):
    store, _ = setup(tmp_path)
    for kwargs in ({"byte_limit": 1}, {"record_limit": 1}):
        with pytest.raises(Failure, match="LIMIT"):
            backup(store, **kwargs)

    class ShortOutput:
        def write(self, raw):
            return len(raw) - 1

    with pytest.raises(Failure, match="STORAGE"):
        export_catalog(store, ShortOutput(), access=ACCESS)

    def forbidden():
        raise AssertionError("unauthorized catalog read")

    monkeypatch.setattr(store, "_connect", forbidden)
    with pytest.raises(Failure, match="AUTHORITY"):
        export_catalog(store, BytesIO(), access=replace(ACCESS, permissions=frozenset()))


@pytest.mark.parametrize(
    "change",
    [
        {"scope": "bad/scope"},
        {"digest": "z" * 64},
        {"length": MAX_BYTES + 1},
        {"records": MAX_RECORDS + 1},
        {"revision": True},
    ],
)
def test_checkpoint_validation(change):
    with pytest.raises(Failure):
        Checkpoint(
            **(
                dict(
                    namespace="operator",
                    scope="mission",
                    revision=1,
                    height=0,
                    digest="a" * 64,
                    length=1,
                    records=1,
                )
                | change
            )
        )


def test_checkpoint_parser_rejects_unknown_fields_versions_and_oversize(tmp_path):
    store, _ = setup(tmp_path)
    _, checkpoint = backup(store)
    for raw in (
        b" " * (MAX_LINE + 1),
        dumps(checkpoint.record() | {"version": "future"}),
        dumps(checkpoint.record() | {"extra": 1}),
    ):
        with pytest.raises(Failure):
            decode_checkpoint(raw)


def test_noncanonical_and_misbehaving_streams(tmp_path):
    store, _ = setup(tmp_path)
    body, checkpoint = backup(store)
    padded = b" " + body
    with pytest.raises(Failure, match="SHAPE"):
        restore(tmp_path, store, padded, reanchor(padded, checkpoint))

    class InvalidStream:
        def __init__(self, reply):
            self.reply = reply

        def readline(self, bound):
            return self.reply

    for reply in ("not bytes", b"x" * (MAX_LINE + 2)):
        with pytest.raises(Failure):
            restore_catalog(
                InvalidStream(reply),
                tmp_path / "restored",
                store.provider,
                checkpoint=checkpoint,
                current_revision=checkpoint.revision,
                namespace="operator",
                scope="mission",
                access=ACCESS,
            )
    assert not (tmp_path / "restored").exists()


def test_activation_failure_keeps_source_and_cleans_owned_staging(tmp_path, monkeypatch):
    store, _ = setup(tmp_path)
    body, checkpoint = backup(store)

    def fail_activation(*args):
        raise OSError("fixture rename failure")

    monkeypatch.setattr("checkedflow.retention_backup.os.replace", fail_activation)
    with pytest.raises(OSError, match="fixture"):
        restore(tmp_path, store, body, checkpoint)
    assert not (tmp_path / "restored").exists()
    assert store.get(reference(b"live"), access=ACCESS) == b"live"


def test_process_crash_before_activation_leaves_no_active_catalog(tmp_path):
    store, _ = setup(tmp_path)
    body, checkpoint = backup(store)
    (tmp_path / "backup.jsonl").write_bytes(body)
    (tmp_path / "checkpoint.json").write_bytes(dumps(checkpoint.record()))
    program = """
import os, sys
from pathlib import Path
from checkedflow.artifact_io import Access
from checkedflow.artifacts import LocalStore
from checkedflow.retention_backup import decode_checkpoint, restore_catalog
import checkedflow.retention_backup as backup
root = Path(sys.argv[1])
checkpoint = decode_checkpoint((root / 'checkpoint.json').read_bytes())
def crash(*args):
    os._exit(97)
backup.os.replace = crash
with (root / 'backup.jsonl').open('rb') as source:
    restore_catalog(source, root / 'restored', LocalStore(root / 'bytes.sqlite'),
        checkpoint=checkpoint, current_revision=checkpoint.revision,
        namespace='operator', scope='mission',
        access=Access('owner', frozenset({'mission'}), frozenset({'restore'})))
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)],
        capture_output=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 97, result.stderr.decode()
    assert not (tmp_path / "restored/catalog.sqlite").exists()
    assert (tmp_path / "restored/pending.sqlite").exists()
    with pytest.raises(FileExistsError):
        restore(tmp_path, store, body, checkpoint)
    assert store.get(reference(b"live"), access=ACCESS) == b"live"


def test_empty_catalog_portable_vector_and_schema(tmp_path):
    resources = files("checkedflow").joinpath("data")
    vector = json.loads(resources.joinpath("retention-backup-vector.json").read_text())
    schema = json.loads(resources.joinpath("retention-checkpoint.schema.json").read_text())
    Draft202012Validator(schema).validate(vector["checkpoint"])
    checkpoint = decode_checkpoint(dumps(vector["checkpoint"]))
    body = vector["jsonl"].encode("utf-8")
    store = restore_catalog(
        BytesIO(body),
        tmp_path / "restored",
        LocalStore(tmp_path / "bytes.sqlite"),
        checkpoint=checkpoint,
        current_revision=1,
        namespace="operator",
        scope="mission",
        access=ACCESS,
    )
    assert backup(store) == (body, checkpoint)
    assert not store.plan(access=ACCESS).objects
