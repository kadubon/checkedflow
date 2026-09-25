"""RPC SDK for an operator's own validating full node."""

import base64
import ipaddress
from urllib.parse import urlparse

import httpx

from checkedflow.core.model import State
from checkedflow.core.values import Object, obj, require, text
from checkedflow.serialization import decode
from checkedflow.wire import document, dumps


class Client:
    def __init__(self, url: str, *, timeout: float = 30) -> None:
        parsed = urlparse(url)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            loopback = False
        require(
            parsed.scheme == "http" and loopback,
            "TRUST",
            "v1 clients require their own loopback full node (or explicit SSH tunnel)",
        )
        require(
            not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment,
            "ADDRESS",
            "invalid RPC endpoint",
        )
        self.url, self.timeout = url.rstrip("/"), timeout

    def rpc(self, method: str, params: Object) -> Object:
        with httpx.Client(timeout=self.timeout, trust_env=False, follow_redirects=False) as client:
            response = client.post(
                self.url,
                content=dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    string_limit=16777216,
                ),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            value = document(response.content, string_limit=16777216)
            require("error" not in value, "RPC", str(value.get("error")))
            return obj(value["result"])

    def state(self) -> State:
        result = self.rpc("abci_query", {"path": "/state", "data": "", "prove": False})
        response = obj(result["response"])
        require(response.get("code", 0) == 0, "RPC", "state query failed")
        raw = base64.b64decode(text(response["value"], limit=16777216), validate=True)
        return decode(document(raw))

    def submit(self, envelope: Object) -> Object:
        result = self.rpc(
            "broadcast_tx_commit", {"tx": base64.b64encode(dumps(envelope)).decode("ascii")}
        )
        check = obj(result.get("check_tx", {}))
        require(bool(check), "OUTCOME_UNKNOWN", "transaction admission response missing")
        require(check.get("code", 0) == 0, "REJECTED", str(check.get("codespace", "check failed")))
        execution = obj(result.get("tx_result", result.get("deliver_tx", {})))
        require(bool(execution), "OUTCOME_UNKNOWN", "transaction execution response missing")
        require(
            execution.get("code", 0) == 0,
            "REJECTED",
            str(execution.get("codespace", "unconfirmed transaction")),
        )
        return result
