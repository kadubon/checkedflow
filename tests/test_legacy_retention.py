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
from checkedflow.legacy_inventory import Checkpoint
from checkedflow.legacy_retention import authenticate_history, preserve, verify
from checkedflow.wire import digest, document, dumps


def initial_checkpoint():
    value = document(files("checkedflow").joinpath("data/legacy-v1.json").read_bytes())["initial"]
    return Checkpoint(value["chain"], value["height"], digest(value))


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
    retained = preserve(raw, trusted, (ref,), store, access=ACCESS, initial=initial_checkpoint())
    assert (
        preserve(raw, trusted, (ref,), store, access=ACCESS, initial=initial_checkpoint())
        == retained
    )
    store.advance(1000, access=ACCESS)
    assert not store.plan(access=ACCESS).objects
    restored = catalog(tmp_path, trusted_floor=store.revision(access=ACCESS))
    verify(retained, trusted, restored, access=ACCESS, initial=initial_checkpoint())
    restored.release(retained.pin, access=ACCESS)
    with pytest.raises(Failure, match="BINDING"):
        verify(retained, trusted, restored, access=ACCESS, initial=initial_checkpoint())
    restored.advance(2000, access=ACCESS)
    assert {item.digest for item in restored.plan(access=ACCESS).objects} == {
        ref.digest,
        retained.snapshot.digest,
    }


def test_bad_checkpoint_or_missing_history_cannot_establish_pin(tmp_path):
    raw, trusted, store, ref = fixture(tmp_path)
    revision = store.revision(access=ACCESS)
    with pytest.raises(Failure, match="CHECKPOINT"):
        preserve(
            raw,
            replace(trusted, state_hash="0" * 64),
            (ref,),
            store,
            access=ACCESS,
            initial=initial_checkpoint(),
        )
    for history in (
        (),
        (ref, ref),
        (replace(ref, manifest="0" * 64),),
        (replace(ref, digest="0" * 64),),
        (replace(ref, kind="evidence"),),
    ):
        with pytest.raises(Failure):
            preserve(raw, trusted, history, store, access=ACCESS, initial=initial_checkpoint())
    assert store.revision(access=ACCESS) == revision


def test_verification_checks_pin_identity_reference_set_and_fresh_bytes(tmp_path, monkeypatch):
    raw, trusted, store, ref = fixture(tmp_path)
    retained = preserve(raw, trusted, (ref,), store, access=ACCESS, initial=initial_checkpoint())
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
            verify(changed, trusted, store, access=ACCESS, initial=initial_checkpoint())
    with pytest.raises(Failure, match="LIMIT"):
        store.verify_pin(retained.pin, (), access=ACCESS)
    monkeypatch.setattr(store.provider, "get", lambda *a, **kw: b"corrupt")
    with pytest.raises(Failure):
        verify(retained, trusted, store, access=ACCESS, initial=initial_checkpoint())


def publish_chunk(store, trusted, chunk):
    raw = dumps(chunk)
    ref = Reference(
        "sha256",
        sha256(raw).hexdigest(),
        len(raw),
        "application/json",
        "archive",
        "mission",
        trusted.state_hash,
    )
    store.put(ref, BytesIO(raw), access=ACCESS)
    return ref


def test_ordered_chunks_replay_exact_history_and_reject_reordering(tmp_path):
    from jsonschema import Draft202012Validator

    from checkedflow.recovery import replay_blocks
    from checkedflow.serialization import decode, encode

    raw, trusted, store, ref = fixture(tmp_path)
    history = document(store.get(ref, access=ACCESS))
    Draft202012Validator(
        document(files("checkedflow").joinpath("data/legacy-history.schema.json").read_bytes())
    ).validate(history)
    split = len(history["blocks"]) // 2
    middle = replay_blocks(decode(history["initial"]), history["blocks"][:split]).state
    first = publish_chunk(
        store, trusted, {"initial": history["initial"], "blocks": history["blocks"][:split]}
    )
    second = publish_chunk(
        store, trusted, {"initial": encode(middle), "blocks": history["blocks"][split:]}
    )
    retained = preserve(
        raw, trusted, (first, second), store, access=ACCESS, initial=initial_checkpoint()
    )
    verify(retained, trusted, store, access=ACCESS, initial=initial_checkpoint())
    with pytest.raises(Failure, match="CHECKPOINT"):
        authenticate_history((second, first), initial_checkpoint(), trusted, store, access=ACCESS)
    with pytest.raises(Failure, match="CHECKPOINT"):
        authenticate_history((first,), initial_checkpoint(), trusted, store, access=ACCESS)


@pytest.mark.parametrize("fault", ["empty", "outcome", "gap", "genesis", "extra", "duplicate-key"])
def test_bad_history_rejected_before_snapshot_publication(tmp_path, fault):
    raw, trusted, store, ref = fixture(tmp_path)
    chunk = document(store.get(ref, access=ACCESS))
    if fault == "empty":
        chunk["blocks"] = []
    elif fault == "outcome":
        next(block for block in chunk["blocks"] if block["transactions"])["transactions"][0][
            "code"
        ] = "FORGED"
    elif fault == "gap":
        chunk["blocks"][0]["height"] += 1
    elif fault == "genesis":
        chunk["initial"]["chain"] = "other-chain"
    elif fault == "extra":
        chunk["blocks"].append({"height": trusted.height + 1, "transactions": []})
    if fault == "duplicate-key":
        body = b'{"initial":{},"initial":{},"blocks":[]}'
        ref = Reference(
            "sha256",
            sha256(body).hexdigest(),
            len(body),
            "application/json",
            "archive",
            "mission",
            trusted.state_hash,
        )
        store.put(ref, BytesIO(body), access=ACCESS)
    else:
        ref = publish_chunk(store, trusted, chunk)
    revision = store.revision(access=ACCESS)
    with pytest.raises(Failure):
        preserve(raw, trusted, (ref,), store, access=ACCESS, initial=initial_checkpoint())
    assert store.revision(access=ACCESS) == revision


def test_history_requires_independent_genesis_and_strict_final_height(tmp_path):
    _, trusted, store, ref = fixture(tmp_path)
    for initial in (
        replace(initial_checkpoint(), height=1),
        replace(initial_checkpoint(), chain="other"),
        replace(initial_checkpoint(), state_hash="0" * 64),
    ):
        with pytest.raises(Failure):
            authenticate_history((ref,), initial, trusted, store, access=ACCESS)
    with pytest.raises(Failure):
        authenticate_history(
            (ref,), initial_checkpoint(), replace(trusted, height=True), store, access=ACCESS
        )
