"""ABCI boundaries, independent transient execution and atomic crash recovery."""

from checkedflow.distributed.application import Application
from checkedflow.distributed.proto.generated.tendermint.abci import types_pb2 as pb
from checkedflow.serialization import encode
from checkedflow.storage import Store
from checkedflow.wire import digest, dumps


def test_finalize_before_commit_and_crash_replay(h, tmp_path):
    store = Store(tmp_path / "state.sqlite3", h.initial)
    app = Application(store)
    raw = dumps(h.events[0][0])
    assert app.CheckTx(pb.RequestCheckTx(tx=raw), None).code == 0
    assert app.ProcessProposal(pb.RequestProcessProposal(height=1, txs=[raw]), None).status == 1
    assert app.PrepareProposal(
        pb.RequestPrepareProposal(height=1, txs=[b"bad", raw], max_tx_bytes=1048576), None
    ).txs == [raw]
    result = app.FinalizeBlock(pb.RequestFinalizeBlock(height=1, txs=[raw]), None)
    assert app.Info(pb.RequestInfo(), None).last_block_height == 0
    assert store.load() == h.initial
    # Abrupt application restart loses transient state; CometBFT replays the finalized block.
    recovered = Application(Store(store.path, h.initial))
    again = recovered.FinalizeBlock(pb.RequestFinalizeBlock(height=1, txs=[raw]), None)
    assert again.app_hash == result.app_hash
    recovered.Commit(pb.RequestCommit(), None)
    assert recovered.Info(pb.RequestInfo(), None).last_block_height == 1
    assert recovered.Info(pb.RequestInfo(), None).last_block_app_hash.hex() == digest(
        encode(store.load())
    )
    assert Application(Store(store.path, h.initial)).committed == recovered.committed


def test_invalid_proposal_never_mutates_committed_state(h, tmp_path):
    app = Application(Store(tmp_path / "state.sqlite3", h.initial))
    raw = b'{"malformed":true}'
    assert app.ProcessProposal(pb.RequestProcessProposal(height=1, txs=[raw]), None).status == 2
    assert app.CheckTx(pb.RequestCheckTx(tx=raw), None).code == 1
    assert app.committed == h.initial
    result = app.FinalizeBlock(pb.RequestFinalizeBlock(height=1, txs=[raw]), None)
    assert result.tx_results[0].code == 1
    app.Commit(pb.RequestCommit(), None)
    assert app.committed.height == 1 and not app.committed.workers
    assert app.Query(pb.RequestQuery(path="/state", prove=True), None).code == 1


def test_two_conflicting_leases_cannot_both_survive_proposal(h, tmp_path):
    h.task(start=False)
    app = Application(Store(tmp_path / "state.sqlite3", h.runtime.state))
    first = dumps(h.envelope("task.lease", {"id": "t"}, actor="w0"))
    second = dumps(h.envelope("task.lease", {"id": "t"}, actor="w1", command_id="competing"))
    height = h.runtime.state.height + 1
    assert all(app.CheckTx(pb.RequestCheckTx(tx=tx), None).code == 0 for tx in (first, second))
    prepared = app.PrepareProposal(
        pb.RequestPrepareProposal(height=height, txs=[first, second], max_tx_bytes=1048576), None
    )
    assert list(prepared.txs) == [first]
    assert (
        app.ProcessProposal(
            pb.RequestProcessProposal(height=height, txs=[first, second]), None
        ).status
        == 2
    )


def test_original_transaction_bytes_are_bounded_before_canonicalization(h, tmp_path):
    app = Application(Store(tmp_path / "state.sqlite3", h.initial))
    raw = dumps(h.events[0][0])
    at_limit = raw + b" " * (1048576 - len(raw))
    oversized = at_limit + b" "
    rejection = app.CheckTx(pb.RequestCheckTx(tx=oversized), None)
    assert rejection.code == 1 and rejection.codespace == "LIMIT"
    assert (
        app.ProcessProposal(pb.RequestProcessProposal(height=1, txs=[oversized]), None).status
        == pb.ResponseProcessProposal.REJECT
    )
    assert not app.PrepareProposal(
        pb.RequestPrepareProposal(height=1, txs=[oversized], max_tx_bytes=2097152), None
    ).txs
    assert app.CheckTx(pb.RequestCheckTx(tx=at_limit), None).code == 0
    result = app.FinalizeBlock(pb.RequestFinalizeBlock(height=1, txs=[at_limit]), None)
    assert result.tx_results[0].code == 0
    app.Commit(pb.RequestCommit(), None)
    assert app.store.blocks()[0]["transactions"][0]["raw"] == at_limit.hex()
