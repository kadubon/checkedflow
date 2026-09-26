"""Raw-byte v2 RPC client for an operator-controlled loopback validating node."""

import base64

import httpx

from checkedflow.core.operational import State
from checkedflow.core.values import Failure, Object, integer, obj, require, text
from checkedflow.distributed.client import Client as Transport
from checkedflow.operational_codec import MAX_STATE_BYTES, decode
from checkedflow.operational_storage import _inputs
from checkedflow.wire import document, dumps

MAX_RPC_BYTES = 8 * 1048576


class Client:
    def __init__(self, url: str, *, chain: str, timeout: float = 30) -> None:
        require(
            type(timeout) in {int, float} and 0 < timeout <= 60,
            "TIMEOUT",
            "bounded RPC timeout required",
        )
        self.transport = Transport(url, timeout=timeout)
        self.chain = text(chain, limit=128)

    def rpc(self, method: str, params: Object) -> Object:
        with (
            httpx.Client(
                timeout=self.transport.timeout, trust_env=False, follow_redirects=False
            ) as client,
            client.stream(
                "POST",
                self.transport.url,
                content=dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    string_limit=MAX_RPC_BYTES,
                ),
                headers={"Content-Type": "application/json", "Accept-Encoding": "identity"},
            ) as response,
        ):
            response.raise_for_status()
            require(
                response.headers.get("content-encoding", "identity") == "identity",
                "RPC",
                "compressed RPC response unsupported",
            )
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                require(len(body) + len(chunk) <= MAX_RPC_BYTES, "RPC", "RPC response ceiling")
                body.extend(chunk)
        value = document(bytes(body), string_limit=MAX_RPC_BYTES)
        require("error" not in value and "result" in value, "RPC", "RPC response rejected")
        return obj(value["result"])

    def state(self) -> State:
        result = self.rpc("abci_query", {"path": "/v2/state", "data": "", "prove": False})
        response = obj(result.get("response", {}))
        require(
            type(response.get("code")) is int and response["code"] == 0, "RPC", "state query failed"
        )
        require("value" in response and "height" in response, "RPC", "incomplete state response")
        encoded = text(response["value"], limit=4 * ((MAX_STATE_BYTES + 2) // 3))
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise Failure("RPC", "invalid state encoding") from exc
        state = decode(raw)
        require(state.chain == self.chain, "CHAIN", "own-node chain differs")
        reported_height = text(response["height"], limit=20)
        require(reported_height == str(state.height), "RPC", "state response height differs")
        return state

    def live_state(self) -> State:
        """Read own-node sync status before state; this is not task/effect authorization.

        Use with a local progress watchdog. A single non-catching-up response does not establish
        continuing quorum or independent freshness; only the configured validating node is trusted.
        """
        status = self.rpc("status", {})
        require(
            obj(status.get("node_info")).get("network") == self.chain,
            "CHAIN",
            "own-node status chain differs",
        )
        sync = obj(status.get("sync_info"))
        require(sync.get("catching_up") is False, "NOT_READY", "own node not synchronized")
        spelling = text(sync.get("latest_block_height"), limit=20)
        require(spelling.isascii() and spelling.isdecimal(), "RPC", "invalid status height")
        height = integer(int(spelling), low=1)
        require(str(height) == spelling, "RPC", "status height spelling")
        state = self.state()
        require(state.height >= height, "STALE", "state precedes observed own-node height")
        return state

    def submit(self, raw: bytes) -> Object:
        _inputs((raw,))
        try:
            result = self.rpc("broadcast_tx_commit", {"tx": base64.b64encode(raw).decode("ascii")})
        except (Failure, httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
            raise Failure(
                "OUTCOME_UNKNOWN", "query committed task and nonce before retrying"
            ) from exc
        try:
            check = obj(result["check_tx"])
            require(integer(check["code"]) == 0, "REJECTED", "transaction admission rejected")
            execution = obj(result.get("tx_result", result.get("deliver_tx", {})))
            require(integer(execution["code"]) == 0, "REJECTED", "transaction execution rejected")
            # This confirms a committed command, not execution or artifact acceptance.
            reported = text(result["height"], limit=20)
            require(reported.isascii() and reported.isdecimal(), "RPC", "commit height missing")
            require(str(integer(int(reported), low=1)) == reported, "RPC", "commit height spelling")
        except Failure as exc:
            if exc.code == "REJECTED":
                raise
            raise Failure(
                "OUTCOME_UNKNOWN", "malformed commit response; reconcile before retrying"
            ) from exc
        except (KeyError, ValueError, TypeError) as exc:
            raise Failure(
                "OUTCOME_UNKNOWN", "malformed commit response; reconcile before retrying"
            ) from exc
        return result
