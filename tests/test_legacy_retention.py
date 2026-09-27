"""Actual catalog roots protect old snapshot/history; no inference of history coverage."""

from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
from io import BytesIO

import pytest
from test_legacy_successor import source
from test_retention import ACCESS, catalog

from checkedflow.core.artifact import Reference
from checkedflow.core.values import Failure
from checkedflow.legacy_retention import preserve, verify
from checkedflow.wire import document, dumps


def fixture(tmp_path):
    raw, trusted = source()
    capture = document(files("checkedflow").joinpath("data/legacy-v1.json").read_bytes())
    archive = dumps({"initial": capture["initial"], "blocks": capture["blocks"]})
    store = catalog(tmp_path)
    ref = Reference(
        "sha256",
        sha256(archive).hexdigest(),
        len(archive),
        "application/json",
        "archive",
        "mission",
        trusted.state_hash,
    )
    store.put(ref, BytesIO(archive), access=ACCESS)
    return raw, trusted, store, ref


def test_pin_retains_snapshot_and_history_across_reopen_and_time(tmp_path):
    raw, trusted, store, ref = fixture(tmp_path)
    retained = preserve(raw, trusted, (ref,), store, access=ACCESS)
    assert preserve(raw, trusted, (ref,), store, access=ACCESS) == retained
    store.advance(1000, access=ACCESS)
    assert not store.plan(access=ACCESS).objects
    restored = catalog(tmp_path, trusted_floor=store.revision(access=ACCESS))
    verify(retained, trusted, restored, access=ACCESS)
    restored.release(retained.pin, access=ACCESS)
    with pytest.raises(Failure, match="BINDING"):
        verify(retained, trusted, restored, access=ACCESS)
    restored.advance(2000, access=ACCESS)
    assert {item.digest for item in restored.plan(access=ACCESS).objects} == {
        ref.digest,
        retained.snapshot.digest,
    }


def test_bad_checkpoint_or_missing_history_cannot_establish_pin(tmp_path):
    raw, trusted, store, ref = fixture(tmp_path)
    revision = store.revision(access=ACCESS)
    with pytest.raises(Failure, match="CHECKPOINT"):
        preserve(raw, replace(trusted, state_hash="0" * 64), (ref,), store, access=ACCESS)
    for history in (
        (),
        (ref, ref),
        (replace(ref, manifest="0" * 64),),
        (replace(ref, digest="0" * 64),),
        (replace(ref, kind="evidence"),),
    ):
        with pytest.raises(Failure):
            preserve(raw, trusted, history, store, access=ACCESS)
    assert store.revision(access=ACCESS) == revision


def test_verification_checks_pin_identity_reference_set_and_fresh_bytes(tmp_path, monkeypatch):
    raw, trusted, store, ref = fixture(tmp_path)
    retained = preserve(raw, trusted, (ref,), store, access=ACCESS)
    for changed in (
        replace(retained, pin=replace(retained.pin, sequence=retained.pin.sequence + 1)),
        replace(retained, pin=replace(retained.pin, principal="other")),
        replace(retained, pin=replace(retained.pin, identity="other")),
        replace(retained, history=()),
        replace(retained, history=(ref, ref)),
        replace(retained, history=(replace(ref, manifest="0" * 64),)),
        replace(retained, history=(replace(ref, digest="0" * 64),)),
    ):
        with pytest.raises(Failure):
            verify(changed, trusted, store, access=ACCESS)
    with pytest.raises(Failure, match="LIMIT"):
        store.verify_pin(retained.pin, (), access=ACCESS)
    monkeypatch.setattr(store.provider, "get", lambda *a, **kw: b"corrupt")
    with pytest.raises(Failure):
        verify(retained, trusted, store, access=ACCESS)
