"""CometBFT 0.40 ABCI: candidate execution is transient until Commit."""

import threading
from concurrent import futures
from pathlib import Path
from typing import cast

import grpc

from checkedflow.core.model import State
from checkedflow.core.values import JSON, Failure, require
from checkedflow.distributed.proto.generated.tendermint.abci import types_pb2 as pb
from checkedflow.distributed.proto.generated.tendermint.abci import types_pb2_grpc as rpc
from checkedflow.runtime import Runtime
from checkedflow.serialization import encode
from checkedflow.storage import Store
from checkedflow.wire import digest, document, dumps, transaction_document


class Application(rpc.ABCIServicer):  # type: ignore[misc]
    """Generated gRPC boundary, with an independently testable authenticated runtime."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.committed = store.load()
        self.pending: State | None = None
        self.transactions: list[JSON] = []
        self.lock = threading.RLock()

    def Echo(self, request: pb.RequestEcho, context: grpc.ServicerContext) -> pb.ResponseEcho:
        return pb.ResponseEcho(message=request.message)

    def Flush(self, request: pb.RequestFlush, context: grpc.ServicerContext) -> pb.ResponseFlush:
        return pb.ResponseFlush()

    def Info(self, request: pb.RequestInfo, context: grpc.ServicerContext) -> pb.ResponseInfo:
        with self.lock:
            return pb.ResponseInfo(
                data="CheckedFlow",
                version="0.1.0",
                app_version=1,
                last_block_height=self.committed.height,
                last_block_app_hash=bytes.fromhex(digest(encode(self.committed))),
            )

    def InitChain(
        self, request: pb.RequestInitChain, context: grpc.ServicerContext
    ) -> pb.ResponseInitChain:
        with self.lock:
            require(
                request.chain_id == self.committed.chain and request.initial_height in {0, 1},
                "GENESIS",
                "chain or initial height mismatch",
            )
            keys = [v.pub_key.ed25519.hex() for v in request.validators]
            require(
                set(keys) == set(self.committed.organizations.values()) and len(keys) == 4,
                "GENESIS",
                "validator identities differ from organization registry",
            )
            require(
                len({v.power for v in request.validators}) == 1 and request.validators[0].power > 0,
                "GENESIS",
                "validators must have equal positive power",
            )
            require(
                document(request.app_state_bytes) == encode(self.store.initial),
                "GENESIS",
                "application genesis mismatch",
            )
            return pb.ResponseInitChain(app_hash=bytes.fromhex(digest(encode(self.committed))))

    def CheckTx(
        self, request: pb.RequestCheckTx, context: grpc.ServicerContext
    ) -> pb.ResponseCheckTx:
        with self.lock:
            try:
                runtime = Runtime(self.committed)
                runtime.apply(transaction_document(request.tx), height=self.committed.height + 1)
                return pb.ResponseCheckTx(code=0, gas_wanted=1)
            except Failure as exc:
                return pb.ResponseCheckTx(code=1, codespace=exc.code, log=exc.message)

    def InsertTx(
        self, request: pb.RequestInsertTx, context: grpc.ServicerContext
    ) -> pb.ResponseInsertTx:
        # This profile uses CometBFT's ordinary flood mempool, never an application mempool.
        return pb.ResponseInsertTx(code=1)

    def ReapTxs(
        self, request: pb.RequestReapTxs, context: grpc.ServicerContext
    ) -> pb.ResponseReapTxs:
        return pb.ResponseReapTxs()

    def PrepareProposal(
        self, request: pb.RequestPrepareProposal, context: grpc.ServicerContext
    ) -> pb.ResponsePrepareProposal:
        with self.lock:
            runtime = Runtime(self.committed)
            runtime.tick(request.height)
            selected: list[bytes] = []
            size = 0
            for raw in request.txs:
                if size + len(raw) > request.max_tx_bytes:
                    continue
                try:
                    runtime.apply(transaction_document(raw), height=request.height)
                except Failure:
                    continue
                selected.append(raw)
                size += len(raw)
            return pb.ResponsePrepareProposal(txs=selected)

    def ProcessProposal(
        self, request: pb.RequestProcessProposal, context: grpc.ServicerContext
    ) -> pb.ResponseProcessProposal:
        with self.lock:
            try:
                runtime = Runtime(self.committed)
                runtime.tick(request.height)
                for raw in request.txs:
                    runtime.apply(transaction_document(raw), height=request.height)
                return pb.ResponseProcessProposal(status=pb.ResponseProcessProposal.ACCEPT)
            except Failure:
                return pb.ResponseProcessProposal(status=pb.ResponseProcessProposal.REJECT)

    def FinalizeBlock(
        self, request: pb.RequestFinalizeBlock, context: grpc.ServicerContext
    ) -> pb.ResponseFinalizeBlock:
        with self.lock:
            require(
                request.height == self.committed.height + 1, "HEIGHT", "unexpected finalized height"
            )
            runtime = Runtime(self.committed)
            runtime.tick(request.height)
            results: list[pb.ExecTxResult] = []
            transactions: list[JSON] = []
            for raw in request.txs:
                try:
                    envelope = transaction_document(raw)
                    runtime.apply(envelope, height=request.height)
                    transactions.append({"raw": raw.hex(), "code": "OK"})
                    results.append(pb.ExecTxResult(code=0, gas_wanted=1, gas_used=1))
                except Failure as exc:
                    transactions.append({"raw": raw.hex(), "code": exc.code})
                    results.append(
                        pb.ExecTxResult(code=1, codespace=exc.code, data=dumps({"code": exc.code}))
                    )
            self.pending, self.transactions = runtime.state, transactions
            return pb.ResponseFinalizeBlock(
                tx_results=results, app_hash=bytes.fromhex(runtime.state_hash)
            )

    def Commit(self, request: pb.RequestCommit, context: grpc.ServicerContext) -> pb.ResponseCommit:
        with self.lock:
            require(self.pending is not None, "STATE", "no finalized block")
            pending = cast(State, self.pending)
            self.store.commit(pending, self.transactions)
            self.committed = pending
            self.pending = None
            self.transactions = []
            return pb.ResponseCommit(retain_height=0)

    def Query(self, request: pb.RequestQuery, context: grpc.ServicerContext) -> pb.ResponseQuery:
        with self.lock:
            if request.prove or request.height not in {0, self.committed.height}:
                return pb.ResponseQuery(
                    code=1, codespace="QUERY", log="historical and proof queries unavailable"
                )
            if request.path == "/state":
                return pb.ResponseQuery(
                    value=dumps(encode(self.committed)), height=self.committed.height
                )
            if request.path == "/hash":
                return pb.ResponseQuery(
                    value=dumps({"hash": digest(encode(self.committed))}),
                    height=self.committed.height,
                )
            return pb.ResponseQuery(code=1, codespace="QUERY", log="unknown query")

    def ExtendVote(
        self, request: pb.RequestExtendVote, context: grpc.ServicerContext
    ) -> pb.ResponseExtendVote:
        return pb.ResponseExtendVote()

    def VerifyVoteExtension(
        self, request: pb.RequestVerifyVoteExtension, context: grpc.ServicerContext
    ) -> pb.ResponseVerifyVoteExtension:
        return pb.ResponseVerifyVoteExtension(status=pb.ResponseVerifyVoteExtension.ACCEPT)

    def ListSnapshots(
        self, request: pb.RequestListSnapshots, context: grpc.ServicerContext
    ) -> pb.ResponseListSnapshots:
        return pb.ResponseListSnapshots()

    def OfferSnapshot(
        self, request: pb.RequestOfferSnapshot, context: grpc.ServicerContext
    ) -> pb.ResponseOfferSnapshot:
        return pb.ResponseOfferSnapshot(result=pb.ResponseOfferSnapshot.REJECT)

    def LoadSnapshotChunk(
        self, request: pb.RequestLoadSnapshotChunk, context: grpc.ServicerContext
    ) -> pb.ResponseLoadSnapshotChunk:
        return pb.ResponseLoadSnapshotChunk()

    def ApplySnapshotChunk(
        self, request: pb.RequestApplySnapshotChunk, context: grpc.ServicerContext
    ) -> pb.ResponseApplySnapshotChunk:
        return pb.ResponseApplySnapshotChunk(result=pb.ResponseApplySnapshotChunk.ABORT)


def serve(database: Path, state: State, address: str) -> None:
    require(address.startswith("127.0.0.1:"), "ADDRESS", "ABCI must bind to loopback")
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4),
        options=[
            ("grpc.max_receive_message_length", 8 * 1048576),
            ("grpc.max_send_message_length", 16 * 1048576),
        ],
    )
    rpc.add_ABCIServicer_to_server(Application(Store(database, state)), server)
    require(server.add_insecure_port(address) > 0, "ADDRESS", "cannot bind ABCI port")
    server.start()
    try:
        server.wait_for_termination()
    finally:
        server.stop(grace=2)
