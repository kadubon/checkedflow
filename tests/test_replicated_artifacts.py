"""Real independent local stores; failure fixtures never qualify separate-host durability."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from importlib.resources import files
from io import BytesIO

import pytest
from jsonschema import Draft202012Validator
from test_artifacts import ACCESS, ref

from checkedflow.artifacts import Access, LocalStore
from checkedflow.core.values import Failure
from checkedflow.replicated_artifacts import Availability, Replica, ReplicatedStore
from checkedflow.wire import document


def fixture(tmp_path):
    return ReplicatedStore(
        tuple(
            Replica(f"operator-{i}", LocalStore(tmp_path / f"replica-{i}.sqlite")) for i in range(4)
        )
    )


def broken(*args, **kwargs):
    raise OSError("private provider address and credential")


def test_publication_requires_fresh_copies_and_survives_one_loss(tmp_path):
    store = fixture(tmp_path)
    store.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    observed = store.inspect(ref(), access=ACCESS)
    assert observed.usable and observed.verified == tuple(r.name for r in store.replicas)
    assert not observed.unavailable
    erase = replace(ACCESS, permissions=frozenset({"erase"}))
    store.replicas[0].store.erase(ref(), access=erase)
    assert store.get(ref(), access=ACCESS) == b"artifact"
    store.replicas[1].store.erase(ref(), access=erase)
    assert not store.inspect(ref(), access=ACCESS).usable
    with pytest.raises(Failure, match="UNAVAILABLE"):
        store.get(ref(), access=ACCESS)
    # The earlier observation remains historical; it cannot serve subsequent reads.
    assert observed.usable
    assert store.replicas[2].store.get(ref(), access=ACCESS) == b"artifact"


@pytest.mark.parametrize("failures", [1, 2, 4])
def test_corruption_never_counts_as_a_copy(tmp_path, failures):
    store = fixture(tmp_path)
    store.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    for replica in store.replicas[:failures]:
        with closing(sqlite3.connect(replica.store.path)) as db, db:
            db.execute("UPDATE artifact_objects SET body=?", (b"corrupt!",))
    observed = store.inspect(ref(), access=ACCESS)
    assert len(observed.verified) == 4 - failures and observed.usable == (failures == 1)
    if failures > 1:
        with pytest.raises(Failure, match="UNAVAILABLE"):
            store.get(ref(), access=ACCESS)
    # Publication does not silently overwrite corrupt replicas to manufacture success.
    if failures > 1:
        with pytest.raises(Failure, match="UNAVAILABLE"):
            store.put(ref(), BytesIO(b"artifact"), access=ACCESS)


def test_lost_upload_reply_is_resolved_by_complete_readback(tmp_path, monkeypatch):
    store = fixture(tmp_path)
    calls = []
    for replica in store.replicas:
        put = replica.store.put

        def lost(reference, source, *, access, put=put):
            calls.append(1)
            put(reference, source, access=access)
            raise OSError("lost committed reply")

        monkeypatch.setattr(replica.store, "put", lost)
    store.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    assert len(calls) == 4 and store.get(ref(), access=ACCESS) == b"artifact"


def test_upload_acknowledgement_alone_is_not_publication(tmp_path, monkeypatch):
    store = fixture(tmp_path)
    for replica in store.replicas[:2]:
        monkeypatch.setattr(replica.store, "put", lambda *a, **k: None)
    with pytest.raises(Failure, match="UNAVAILABLE"):
        store.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    assert len(store.inspect(ref(), access=ACCESS).verified) == 2


def test_stale_forged_and_nonbinary_replies_are_reverified(tmp_path, monkeypatch):
    store = fixture(tmp_path)
    for replica, response in zip(
        store.replicas, (b"wrong", bytearray(b"artifact"), "artifact", b"artifact"), strict=True
    ):
        monkeypatch.setattr(replica.store, "get", lambda *a, response=response, **k: response)
    observed = store.inspect(ref(), access=ACCESS)
    assert observed.verified == ("operator-3",) and not observed.usable
    with pytest.raises(Failure, match="UNAVAILABLE"):
        store.get(ref(), access=ACCESS)


def test_backend_outage_is_bounded_and_does_not_leak_details(tmp_path, monkeypatch):
    store = fixture(tmp_path)
    store.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    for replica in store.replicas[:2]:
        monkeypatch.setattr(replica.store, "get", broken)
    observed = store.inspect(ref(), access=ACCESS)
    assert observed.unavailable == ("operator-0", "operator-1")
    assert "private" not in str(observed.record())
    with pytest.raises(Failure, match="UNAVAILABLE"):
        store.get(ref(), access=ACCESS)


@pytest.mark.parametrize("permission", ["get", "inspect", "put"])
def test_scope_denial_precedes_backend_or_stream_access(tmp_path, monkeypatch, permission):
    store = fixture(tmp_path)
    for replica in store.replicas:
        monkeypatch.setattr(replica.store, "get", lambda *a, **k: pytest.fail("unauthorized probe"))
        monkeypatch.setattr(
            replica.store, "put", lambda *a, **k: pytest.fail("unauthorized upload")
        )
    denied = Access("client", frozenset({"another"}), frozenset({"read", "write"}))
    with pytest.raises(Failure, match="AUTHORITY"):
        if permission == "put":
            store.put(ref(), None, access=denied)
        else:
            getattr(store, permission)(ref(), access=denied)


def test_write_only_cannot_implicitly_read_for_publication(tmp_path):
    store = fixture(tmp_path)
    with pytest.raises(Failure, match="AUTHORITY"):
        store.put(ref(), None, access=replace(ACCESS, permissions=frozenset({"write"})))


def test_bad_upload_publishes_nothing_and_empty_object_is_supported(tmp_path):
    store = fixture(tmp_path)
    with pytest.raises(Failure, match="INTEGRITY"):
        store.put(ref(), BytesIO(b"bad"), access=ACCESS)
    assert not store.inspect(ref(), access=ACCESS).verified
    store.put(ref(b""), BytesIO(b""), access=ACCESS)
    assert store.get(ref(b""), access=ACCESS) == b""


def test_replica_configuration_cannot_count_same_instance_twice(tmp_path):
    replicas = fixture(tmp_path).replicas
    for invalid in (
        replicas[:3],
        tuple(reversed(replicas)),
        (replicas[0], replicas[0], *replicas[2:]),
        (replace(replicas[0], name="https://private"), *replicas[1:]),
        (*replicas[:3], replace(replicas[3], store=replicas[0].store)),
    ):
        with pytest.raises(Failure):
            ReplicatedStore(invalid)


def test_process_interrupt_is_not_swallowed(tmp_path, monkeypatch):
    store = fixture(tmp_path)

    def interrupted(*a, **k):
        raise SystemExit()

    monkeypatch.setattr(store.replicas[0].store, "put", interrupted)
    with pytest.raises(SystemExit):
        store.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    monkeypatch.setattr(store.replicas[0].store, "get", interrupted)
    with pytest.raises(SystemExit):
        store.inspect(ref(), access=ACCESS)


@pytest.mark.parametrize("losses", [1, 2])
def test_effect_executor_uses_current_replica_threshold(tmp_path, monkeypatch, losses):
    from test_effect_supervisor import Executor

    e = Executor(tmp_path / "executor", monkeypatch)
    store = fixture(tmp_path)
    access = e.f.dispatcher.access
    inputs = e.f.inputs
    for item in (inputs.base, inputs.patch, inputs.inventory, *inputs.evidence):
        store.put(item, BytesIO(e.f.store.get(item, access=access)), access=access)
    e.f.dispatcher.store = store
    for replica in store.replicas[:losses]:
        monkeypatch.setattr(replica.store, "get", broken)
    if losses == 1:
        assert e.step() == "observed"
        assert sum(method == "POST" for method, _ in e.f.calls) == 1
    else:
        with pytest.raises(Failure, match="UNAVAILABLE"):
            e.step()
        assert not e.sent and not e.f.calls and e.f.h.current.status == "authorized"


def test_observation_contract_rejects_duplicate_or_ambiguous_replica_counts(tmp_path):
    store = fixture(tmp_path)
    schema = document(
        files("checkedflow").joinpath("data/artifact-availability.schema.json").read_bytes()
    )
    validator = Draft202012Validator(schema)
    observed = store.inspect(ref(), access=ACCESS)
    validator.validate(observed.record())
    assert list(validator.iter_errors(observed.record() | {"required": 2}))
    assert list(
        validator.iter_errors(
            observed.record()
            | {"unavailable": ["operator-0\n", "operator-1", "operator-2", "operator-3"]}
        )
    )
    assert list(validator.iter_errors(observed.record() | {"verified": ["operator-0"]}))
    for verified, unavailable in (
        (("a", "a", "b"), ("c",)),
        (("a", "b", "c"), ("c",)),
        (("b", "a", "c"), ("d",)),
        (("a", "b", "c"), ("https://private",)),
    ):
        with pytest.raises(Failure, match="SHAPE"):
            Availability(ref(), verified, unavailable)
