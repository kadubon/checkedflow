"""Separate CometBFT 0.40 ABCI service for the bounded v2 operational state."""

import argparse
import threading
from concurrent import futures
from dataclasses import dataclass
from pathlib import Path

import grpc

from checkedflow import __version__
from checkedflow.core.operational import State
from checkedflow.core.values import Failure, Object, array, fields, obj, require, text
from checkedflow.distributed.proto.generated.tendermint.abci import types_pb2 as pb
from checkedflow.distributed.proto.generated.tendermint.abci import types_pb2_grpc as rpc
from checkedflow.operational_codec import decode, encode, state_bytes
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import MAX_BLOCK_BYTES, MAX_TRANSACTIONS, Store, _inputs
from checkedflow.wire import document, dumps


@dataclass(frozen=True)
class Configuration:
    initial: State
    validators: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        require(
            tuple(org for org, _ in self.validators) == self.initial.organizations,
            "GENESIS",
            "validator organization mapping",
        )
        keys = [key for _, key in self.validators]
        require(len(keys) == 4 and len(set(keys)) == 4, "GENESIS", "four distinct validator keys")
        require(
            all(len(key) == 64 and all(c in "0123456789abcdef" for c in key) for key in keys),
            "GENESIS",
            "validator key encoding",
        )
        require(
            not set(keys) & {credential.public_key for credential in self.initial.credentials},
            "GENESIS",
            "validator and command keys must be separate",
        )

    def encode(self) -> Object:
        return {
            "state": encode(self.initial),
            "validators": [
                {"organization": org, "public_key": key} for org, key in self.validators
            ],
        }

    @classmethod
    def decode(cls, raw: bytes) -> "Configuration":
        value = document(raw)
        fields(value, "state validators")
        validators = []
        for item in array(value["validators"], limit=4):
            row = obj(item)
            fields(row, "organization public_key")
            validators.append(
                (text(row["organization"], limit=80), text(row["public_key"], limit=64))
            )
        return cls(decode(dumps(value["state"])), tuple(validators))


@dataclass(frozen=True)
class Pending:
    height: int
    transactions: tuple[bytes, ...]
    state_hash: str
    outcomes: tuple[str, ...]


class Application(rpc.ABCIServicer):  # type: ignore[misc]
    def __init__(self, store: Store, configuration: Configuration) -> None:
        require(
            store.initial == configuration.initial, "GENESIS", "store and ABCI configuration differ"
        )
        self.store, self.configuration = store, configuration
        self.committed = store.load()
        self.pending: Pending | None = None
        self.lock = threading.RLock()

    def Echo(self, request: pb.RequestEcho, context: grpc.ServicerContext) -> pb.ResponseEcho:
        return pb.ResponseEcho(message=request.message)

    def Flush(self, request: pb.RequestFlush, context: grpc.ServicerContext) -> pb.ResponseFlush:
        return pb.ResponseFlush()

    def Info(self, request: pb.RequestInfo, context: grpc.ServicerContext) -> pb.ResponseInfo:
        with self.lock:
            return pb.ResponseInfo(
                data="CheckedFlow operational v2",
                version=__version__,
                app_version=2,
                last_block_height=self.committed.height,
                last_block_app_hash=bytes.fromhex(Runtime(self.committed).state_hash),
            )

    def InitChain(
        self, request: pb.RequestInitChain, context: grpc.ServicerContext
    ) -> pb.ResponseInitChain:
        with self.lock:
            require(
                request.chain_id == self.committed.chain and request.initial_height in {0, 1},
                "GENESIS",
                "chain or initial height differs",
            )
            keys = [validator.pub_key.ed25519.hex() for validator in request.validators]
            require(
                len(keys) == 4 and set(keys) == {key for _, key in self.configuration.validators},
                "GENESIS",
                "validator keys differ",
            )
            require(
                len({validator.power for validator in request.validators}) == 1
                and request.validators[0].power > 0,
                "GENESIS",
                "equal positive voting power required",
            )
            require(
                document(request.app_state_bytes) == self.configuration.encode(),
                "GENESIS",
                "application genesis differs",
            )
            return pb.ResponseInitChain(app_hash=bytes.fromhex(Runtime(self.committed).state_hash))

    def CheckTx(
        self, request: pb.RequestCheckTx, context: grpc.ServicerContext
    ) -> pb.ResponseCheckTx:
        with self.lock:
            try:
                _inputs((request.tx,))
                runtime = Runtime(self.committed)
                runtime.tick(self.committed.height + 1)
                runtime.apply(request.tx, height=runtime.state.height)
                return pb.ResponseCheckTx(code=0, gas_wanted=1)
            except Failure as exc:
                return pb.ResponseCheckTx(code=1, codespace=exc.code)

    def InsertTx(
        self, request: pb.RequestInsertTx, context: grpc.ServicerContext
    ) -> pb.ResponseInsertTx:
        return pb.ResponseInsertTx(code=1)

    def ReapTxs(
        self, request: pb.RequestReapTxs, context: grpc.ServicerContext
    ) -> pb.ResponseReapTxs:
        return pb.ResponseReapTxs()

    def PrepareProposal(
        self, request: pb.RequestPrepareProposal, context: grpc.ServicerContext
    ) -> pb.ResponsePrepareProposal:
        with self.lock:
            require(
                request.height == self.committed.height + 1, "HEIGHT", "proposal height differs"
            )
            runtime = Runtime(self.committed)
            runtime.tick(request.height)
            selected: list[bytes] = []
            size = 0
            ceiling = min(MAX_BLOCK_BYTES, request.max_tx_bytes)
            for raw in request.txs[:MAX_TRANSACTIONS]:
                if size + len(raw) > ceiling:
                    continue
                try:
                    _inputs((raw,))
                    runtime.apply(raw, height=request.height)
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
                require(
                    request.height == self.committed.height + 1, "HEIGHT", "proposal height differs"
                )
                raws = _inputs(request.txs)
                runtime = Runtime(self.committed)
                runtime.tick(request.height)
                for raw in raws:
                    runtime.apply(raw, height=request.height)
                return pb.ResponseProcessProposal(status=pb.ResponseProcessProposal.ACCEPT)
            except Failure:
                return pb.ResponseProcessProposal(status=pb.ResponseProcessProposal.REJECT)

    def FinalizeBlock(
        self, request: pb.RequestFinalizeBlock, context: grpc.ServicerContext
    ) -> pb.ResponseFinalizeBlock:
        with self.lock:
            require(
                request.height == self.committed.height + 1, "HEIGHT", "finalized height differs"
            )
            raws = _inputs(request.txs)
            require(
                self.pending is None
                or (self.pending.height == request.height and self.pending.transactions == raws),
                "CONFLICT",
                "different block is already finalized",
            )
            runtime = Runtime(self.committed)
            runtime.tick(request.height)
            codes = []
            for raw in raws:
                try:
                    runtime.apply(raw, height=request.height)
                    codes.append("OK")
                except Failure as exc:
                    codes.append(exc.code)
            self.pending = Pending(request.height, raws, runtime.state_hash, tuple(codes))
            return pb.ResponseFinalizeBlock(
                app_hash=bytes.fromhex(runtime.state_hash),
                tx_results=[
                    pb.ExecTxResult(code=0, gas_wanted=1, gas_used=1)
                    if code == "OK"
                    else pb.ExecTxResult(code=1, codespace=code, data=dumps({"code": code}))
                    for code in codes
                ],
            )

    def Commit(self, request: pb.RequestCommit, context: grpc.ServicerContext) -> pb.ResponseCommit:
        with self.lock:
            pending = self.pending
            require(pending is not None, "STATE", "no finalized block")
            if pending is None:
                raise Failure("STATE", "no finalized block")
            result = self.store.commit_block(
                pending.height,
                pending.transactions,
                previous_hash=Runtime(self.committed).state_hash,
            )
            require(
                result.state_hash == pending.state_hash and result.outcomes == pending.outcomes,
                "DIVERGENCE",
                "durable replay differs from finalized state",
            )
            self.committed = self.store.load()
            self.pending = None
            return pb.ResponseCommit(retain_height=0)

    def Query(self, request: pb.RequestQuery, context: grpc.ServicerContext) -> pb.ResponseQuery:
        with self.lock:
            if request.prove or request.height not in {0, self.committed.height}:
                return pb.ResponseQuery(code=1, codespace="QUERY")
            if request.path == "/v2/state":
                return pb.ResponseQuery(
                    value=state_bytes(self.committed), height=self.committed.height
                )
            if request.path == "/v2/hash":
                return pb.ResponseQuery(
                    value=dumps({"hash": Runtime(self.committed).state_hash}),
                    height=self.committed.height,
                )
            return pb.ResponseQuery(code=1, codespace="QUERY")

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


def serve(database: Path, configuration: Configuration, address: str) -> None:
    host, separator, port = address.partition(":")
    require(
        host == "127.0.0.1"
        and separator == ":"
        and port.isascii()
        and port.isdecimal()
        and 1 <= int(port) <= 65535,
        "ADDRESS",
        "ABCI requires a literal loopback TCP port",
    )
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4),
        options=[
            ("grpc.max_receive_message_length", 8 * 1048576),
            ("grpc.max_send_message_length", 8 * 1048576),
        ],
    )
    rpc.add_ABCIServicer_to_server(
        Application(Store(database, configuration.initial), configuration), server
    )
    require(server.add_insecure_port(address) > 0, "ADDRESS", "cannot bind ABCI port")
    server.start()
    try:
        server.wait_for_termination()
    finally:
        server.stop(grace=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--address", required=True)
    args = parser.parse_args()
    serve(args.database, Configuration.decode(args.configuration.read_bytes()), args.address)


if __name__ == "__main__":
    main()
