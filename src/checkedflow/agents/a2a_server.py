"""Official A2A JSON-RPC, HTTP+JSON and gRPC bindings with bounded local admission."""

import asyncio
import hmac
import ipaddress
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import grpc
import grpc.aio
import uvicorn
from a2a.server.request_handlers.grpc_handler import GrpcHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.types.a2a_pb2_grpc import add_A2AServiceServicer_to_server
from starlette.applications import Starlette

from checkedflow.agents.a2a import Handler, card
from checkedflow.agents.gateway import AgentGateway as Gateway
from checkedflow.agents.http import MAX_BODY, Guard
from checkedflow.agents.journal import Journal
from checkedflow.agents.push import Push
from checkedflow.core.values import require


class BearerInterceptor(grpc.aio.ServerInterceptor):  # type: ignore[misc]
    def __init__(self, token: str) -> None:
        self.expected = ("Bearer " + token).encode()

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = await continuation(handler_call_details)
        values = [
            v for k, v in handler_call_details.invocation_metadata if k.lower() == "authorization"
        ]
        if handler is None or (
            len(values) == 1
            and isinstance(values[0], str)
            and hmac.compare_digest(values[0].encode(), self.expected)
        ):
            return handler

        async def deny(request: object, context: grpc.aio.ServicerContext) -> None:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Bearer authentication required")

        async def deny_stream(
            request: object, context: grpc.aio.ServicerContext
        ) -> AsyncIterator[object]:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Bearer authentication required")
            if False:  # The gRPC API requires an async generator for streaming handlers.
                yield None

        factory = (
            grpc.unary_stream_rpc_method_handler
            if handler.response_streaming
            else grpc.unary_unary_rpc_method_handler
        )
        return factory(
            deny_stream if handler.response_streaming else deny,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


def application(
    gateway: Gateway,
    url: str,
    token: str,
    *,
    journal_path: Path | None = None,
    push_hosts: tuple[str, ...] = (),
    grpc_url: str = "",
) -> Guard:
    agent_card = card(url, grpc_url)
    journal = Journal(gateway, journal_path)
    handler = Handler(gateway, journal=journal, agent_card=agent_card, push=Push(push_hosts))

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        server = None
        try:
            if grpc_url:
                server = grpc.aio.server(
                    interceptors=[BearerInterceptor(token)],
                    options=[
                        ("grpc.max_receive_message_length", MAX_BODY),
                        ("grpc.max_send_message_length", 16 * 1048576),
                    ],
                    maximum_concurrent_rpcs=128,
                )
                add_A2AServiceServicer_to_server(GrpcHandler(handler), server)  # type: ignore[no-untyped-call]
                require(server.add_insecure_port(grpc_url) != 0, "ADDRESS", "gRPC bind failed")
                await server.start()
            async with anyio.create_task_group() as group:
                group.start_soon(handler.monitor)
                yield
                group.cancel_scope.cancel()
        finally:
            if server:
                await server.stop(1)
            journal.close()

    routes = (
        create_agent_card_routes(agent_card)
        + create_jsonrpc_routes(handler, "/rpc", enable_v0_3_compat=False)
        + create_rest_routes(handler, path_prefix="/v1", enable_v0_3_compat=False)
    )
    return Guard(Starlette(routes=routes, lifespan=lifespan), token)


def serve(
    gateway: Gateway,
    host: str,
    port: int,
    token: str,
    *,
    journal_path: Path,
    push_hosts: tuple[str, ...] = (),
    grpc_port: int = 0,
) -> None:
    require(ipaddress.ip_address(host).is_loopback, "ADDRESS", "bind a numeric loopback address")
    require(
        1 <= port <= 65535 and 0 <= grpc_port <= 65535 and grpc_port != port,
        "ADDRESS",
        "invalid or conflicting port",
    )
    address = f"[{host}]" if ":" in host else host
    app = application(
        gateway,
        f"http://{address}:{port}/rpc",
        token,
        journal_path=journal_path,
        push_hosts=push_hosts,
        grpc_url=f"{address}:{grpc_port}" if grpc_port else "",
    )
    asyncio.run(
        uvicorn.Server(
            uvicorn.Config(app, host=host, port=port, access_log=False, limit_concurrency=128)
        ).serve()
    )
