"""Bounded RPC parsing; response doubles never qualify consensus."""

import base64
import gzip

import httpx
import pytest
from test_operational_runtime import runtime_and_command

from checkedflow.core.values import Failure
from checkedflow.distributed.operational_client import Client
from checkedflow.operational_codec import state_bytes
from checkedflow.wire import dumps, loads


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        "checkedflow.distributed.operational_client.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_raw_transaction_and_state_chain_height_bindings(monkeypatch):
    runtime, _, _ = runtime_and_command()
    raw = b' {"exact":"bytes"} '

    def handler(request):
        rpc = loads(request.content)
        if rpc["method"] == "broadcast_tx_commit":
            assert base64.b64decode(rpc["params"]["tx"]) == raw
            result = {"check_tx": {"code": 0}, "tx_result": {"code": 0}, "height": "1"}
        else:
            assert rpc["params"]["path"] == "/v2/state"
            result = {
                "response": {
                    "code": 0,
                    "height": "0",
                    "value": base64.b64encode(state_bytes(runtime.state)).decode(),
                }
            }
        return httpx.Response(200, content=dumps({"result": result}, string_limit=8388608))

    transport(monkeypatch, handler)
    client = Client("http://127.0.0.1:12345", chain=runtime.state.chain)
    assert client.submit(raw)["height"] == "1"
    assert client.state() == runtime.state
    client.chain = "wrong"
    with pytest.raises(Failure, match="CHAIN"):
        client.state()


@pytest.mark.parametrize(
    "result,code",
    [
        ({}, "OUTCOME_UNKNOWN"),
        ({"check_tx": {"code": 1}}, "REJECTED"),
        ({"check_tx": {"code": 0}, "tx_result": {"code": 1}}, "REJECTED"),
        ({"check_tx": {"code": 0}, "tx_result": {"code": 0}, "height": "01"}, "OUTCOME_UNKNOWN"),
        ({"check_tx": {"code": False}, "tx_result": {"code": 0}, "height": "1"}, "OUTCOME_UNKNOWN"),
    ],
)
def test_commit_ambiguity_is_not_success(monkeypatch, result, code):
    client = Client("http://127.0.0.1:12345", chain="chain")
    monkeypatch.setattr(client, "rpc", lambda *args: result)
    with pytest.raises(Failure, match=code):
        client.submit(b"{}")


@pytest.mark.parametrize("kind", ["outage", "compression", "ceiling"])
def test_outage_compression_and_response_ceiling(monkeypatch, kind):
    client = Client("http://127.0.0.1:12345", chain="chain")

    def handler(request):
        if kind == "outage":
            raise httpx.ReadTimeout("injected")
        if kind == "compression":
            return httpx.Response(
                200, headers={"content-encoding": "gzip"}, content=gzip.compress(b"{}")
            )
        return httpx.Response(200, content=b"x" * 65)

    if kind == "ceiling":
        monkeypatch.setattr("checkedflow.distributed.operational_client.MAX_RPC_BYTES", 64)
    transport(monkeypatch, handler)
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        client.submit(b"{}")
