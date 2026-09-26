"""v2 ABCI ordering/persistence tests; live CometBFT is a separate required gate."""

from dataclasses import replace
from hashlib import sha256
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from test_work_tasks import Harness

from checkedflow.core.values import Failure
from checkedflow.core.work_budget import Ledger
from checkedflow.distributed.operational_application import Application, Configuration
from checkedflow.distributed.proto.generated.tendermint.abci import types_pb2 as pb
from checkedflow.operational_codec import decode
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import Store
from checkedflow.wire import dumps, loads


def application(tmp_path):
    h = Harness()
    configuration = Configuration(
        h.initial,
        tuple(
            (org, sha256(("validator-" + org).encode()).hexdigest())
            for org in h.initial.organizations
        ),
    )
    return h, Application(Store(tmp_path / "app.sqlite", h.initial), configuration)


def test_validator_keys_are_separate_and_genesis_is_exact(tmp_path):
    h, app = application(tmp_path)
    config = app.configuration
    assert Configuration.decode(dumps(config.encode())) == config
    schema = loads(
        files("checkedflow").joinpath("data/operational-configuration.schema.json").read_bytes()
    )
    Draft202012Validator(schema).validate(config.encode())
    # The embedded structural state contract must track its normative source.
    state_schema = loads(
        files("checkedflow").joinpath("data/operational-state.schema.json").read_bytes()
    )
    assert schema["properties"]["state"] == state_schema
    request = pb.RequestInitChain(
        chain_id=h.initial.chain,
        initial_height=1,
        app_state_bytes=dumps(config.encode()),
        validators=[
            pb.ValidatorUpdate(pub_key={"ed25519": bytes.fromhex(key)}, power=10)
            for _, key in config.validators
        ],
    )
    assert app.InitChain(request, None).app_hash == bytes.fromhex(Runtime(h.initial).state_hash)
    request.validators[0].power = 20
    with pytest.raises(Failure, match="voting power"):
        app.InitChain(request, None)
    request.validators[0].power = 10
    request.chain_id = "wrong"
    with pytest.raises(Failure, match="chain"):
        app.InitChain(request, None)
    with pytest.raises(Failure, match="separate"):
        Configuration(
            h.initial, (("a", h.initial.credentials[0].public_key), *config.validators[1:])
        )
    with pytest.raises(Failure, match="fresh genesis"):
        Store(
            tmp_path / "prefunded.sqlite",
            replace(h.initial, budget=Ledger(100, verification_reserve=20)),
        )


def test_finalize_is_transient_and_commit_replays_exact_bytes(tmp_path):
    h, app = application(tmp_path)
    task, _ = h.prepare()
    h.send("task.lease", {"task": task}, "worker")
    h.send("task.start", {"task": task, "fence": 1}, "worker")
    raw = [item for _, item in h.events]
    assert app.CheckTx(pb.RequestCheckTx(tx=raw[0]), None).code == 0
    assert app.CheckTx(pb.RequestCheckTx(tx=b"{}"), None).code == 1
    prepared = app.PrepareProposal(
        pb.RequestPrepareProposal(height=1, max_tx_bytes=2097152, txs=[b"{}", *raw]), None
    )
    assert list(prepared.txs) == raw
    assert (
        app.ProcessProposal(pb.RequestProcessProposal(height=1, txs=raw), None).status
        == pb.ResponseProcessProposal.ACCEPT
    )
    assert (
        app.ProcessProposal(pb.RequestProcessProposal(height=1, txs=[*raw, b"{}"]), None).status
        == pb.ResponseProcessProposal.REJECT
    )
    result = app.FinalizeBlock(pb.RequestFinalizeBlock(height=1, txs=raw), None)
    assert all(item.code == 0 for item in result.tx_results)
    assert app.store.load().height == 0
    assert decode(app.Query(pb.RequestQuery(path="/v2/state"), None).value).height == 0
    recovered = Application(app.store, app.configuration)
    assert recovered.Info(pb.RequestInfo(), None).last_block_height == 0
    assert recovered.FinalizeBlock(pb.RequestFinalizeBlock(height=1, txs=raw), None) == result
    with pytest.raises(Failure, match="CONFLICT"):
        recovered.FinalizeBlock(pb.RequestFinalizeBlock(height=1, txs=[]), None)
    recovered.Commit(pb.RequestCommit(), None)
    reopened = Application(app.store, app.configuration)
    assert reopened.Info(pb.RequestInfo(), None).last_block_app_hash == result.app_hash
    assert reopened.committed.tasks[0].status == "running"
    assert app.store.verify_history(expected_hash=result.app_hash.hex()) == reopened.committed


def test_failed_commit_does_not_publish_pending_state(tmp_path, monkeypatch):
    h, app = application(tmp_path)
    _, raw = h.send("mission.resume", {})
    app.FinalizeBlock(pb.RequestFinalizeBlock(height=1, txs=[raw]), None)
    original = app.store.commit_block

    def unavailable(*args, **kwargs):
        raise OSError("injected disk failure")

    monkeypatch.setattr(app.store, "commit_block", unavailable)
    with pytest.raises(OSError):
        app.Commit(pb.RequestCommit(), None)
    assert app.committed.height == 0 and app.pending is not None
    monkeypatch.setattr(app.store, "commit_block", original)
    app.Commit(pb.RequestCommit(), None)
    assert app.committed.height == 1 and app.pending is None


def test_bounds_rejections_and_query_visibility(tmp_path):
    _, app = application(tmp_path)
    assert (
        app.ProcessProposal(pb.RequestProcessProposal(height=2), None).status
        == pb.ResponseProcessProposal.REJECT
    )
    assert (
        app.ProcessProposal(pb.RequestProcessProposal(height=1, txs=[b"{}"] * 257), None).status
        == pb.ResponseProcessProposal.REJECT
    )
    assert not app.PrepareProposal(
        pb.RequestPrepareProposal(height=1, max_tx_bytes=0, txs=[b"{}"]), None
    ).txs
    result = app.FinalizeBlock(pb.RequestFinalizeBlock(height=1, txs=[b"{}"]), None)
    assert result.tx_results[0].code == 1
    app.Commit(pb.RequestCommit(), None)
    for request in [
        pb.RequestQuery(path="/state"),
        pb.RequestQuery(path="/v2/state", prove=True),
        pb.RequestQuery(path="/v2/state", height=2),
    ]:
        assert app.Query(request, None).code == 1
    assert (
        loads(app.Query(pb.RequestQuery(path="/v2/hash"), None).value)["hash"]
        == result.app_hash.hex()
    )
    with pytest.raises(Failure, match="no finalized"):
        app.Commit(pb.RequestCommit(), None)
    assert (
        app.OfferSnapshot(pb.RequestOfferSnapshot(), None).result == pb.ResponseOfferSnapshot.REJECT
    )
    assert (
        app.ApplySnapshotChunk(pb.RequestApplySnapshotChunk(), None).result
        == pb.ResponseApplySnapshotChunk.ABORT
    )
