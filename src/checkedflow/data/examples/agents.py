"""Discover a configured gateway, optionally submitting an already signed envelope."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from checkedflow.core.values import Object, obj, text
from checkedflow.wire import dumps, transaction_document


async def a2a_request(url: str, token: str, envelope: str | None) -> Object:
    import httpx
    from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
    from a2a.types import a2a_pb2 as pb
    from google.protobuf.json_format import MessageToDict, ParseDict

    data: Object = {"operation": "profile"}
    identity = "profile-request"
    if envelope is not None:
        parsed = transaction_document(envelope.encode())
        identity = text(obj(parsed["command"])["id"])
        data = {"operation": "submit", "envelopeJson": envelope}
    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer " + token},
        trust_env=False,
        follow_redirects=False,
        timeout=40,
    ) as transport:
        card = await A2ACardResolver(transport, url.rstrip("/")).get_agent_card()
        client = ClientFactory(ClientConfig(httpx_client=transport, streaming=False)).create(card)
        message = pb.Message(
            message_id=identity,
            role=pb.ROLE_USER,
            parts=[ParseDict({"data": data}, pb.Part())],
        )
        async for event in client.send_message(pb.SendMessageRequest(message=message)):
            return obj(MessageToDict(event.message.parts[0].data))
    raise RuntimeError("gateway returned no acknowledgment")


async def mcp_request(rpc: str, chain: str, mission: str, envelope: str | None) -> str:
    from mcp import Client, StdioServerParameters

    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "checkedflow.cli",
            "mcp",
            "--rpc",
            rpc,
            "--chain",
            chain,
            "--mission",
            mission,
        ],
    )
    async with Client(parameters, read_timeout_seconds=40) as client:
        if envelope is None:
            resource = await client.read_resource("checkedflow://profile")
            return resource.model_dump_json(by_alias=True)
        result = await client.call_tool("checkedflow_submit", {"envelope_json": envelope})
        return result.model_dump_json(by_alias=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transport", choices=["a2a", "mcp"])
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--token-env", default="CHECKEDFLOW_AGENT_TOKEN")
    parser.add_argument("--rpc", default="http://127.0.0.1:26657")
    parser.add_argument("--chain", default="checkedflow-lab")
    parser.add_argument("--mission", default="reuse")
    parser.add_argument("--envelope", type=Path, help="optional pre-signed JSON file; submits once")
    args = parser.parse_args()
    envelope = args.envelope.read_text(encoding="utf-8") if args.envelope else None
    if args.transport == "a2a":
        token = os.environ.get(args.token_env, "")
        if not token:
            parser.error(f"set {args.token_env} for the configured gateway")
        print(dumps(asyncio.run(a2a_request(args.url, token, envelope))).decode())
    else:
        print(asyncio.run(mcp_request(args.rpc, args.chain, args.mission, envelope)))


if __name__ == "__main__":
    main()
