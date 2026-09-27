"""Official A2A JSON-RPC, HTTP+JSON and gRPC bindings with bounded local admission."""

import asyncio
import ipaddress
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import grpc
import grpc.aio
import uvicorn
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.grpc_handler import (
    DefaultGrpcServerCallContextBuilder,
    GrpcHandler,
)
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.types.a2a_pb2_grpc import add_A2AServiceServicer_to_server
from starlette.applications import Starlette
from starlette.requests import Request

from checkedflow.agents.a2a import Handler, card
from checkedflow.agents.access import Policy
from checkedflow.agents.authentication import Authentication, authenticated_headers
from checkedflow.agents.download import Downloads, Reader
from checkedflow.agents.gateway import AgentGateway as Gateway
from checkedflow.agents.http import MAX_BODY, Guard
from checkedflow.agents.journal import Journal
from checkedflow.agents.oauth import OAuth
from checkedflow.agents.push import Push
from checkedflow.agents.secrets import Keyring
from checkedflow.agents.tls import MutualTLS, advertised, http_config
from checkedflow.core.values import Failure, require


class BearerInterceptor(grpc.aio.ServerInterceptor):  # type: ignore[misc]
    def __init__(
        self, token: str, oauth: OAuth | None = None, policy: Policy | None = None
    ) -> None:
        self.authentication = Authentication(token, oauth)
        self.policy = policy

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = await continuation(handler_call_details)
        values = [
            v for k, v in handler_call_details.invocation_metadata if k.lower() == "authorization"
        ]
        if handler is None:
            return handler
        authorized = False
        try:
            authenticated_headers(values, self.authentication.verify)
            authorized = True
        except Failure:
            pass

        def recheck() -> None:
            principal = authenticated_headers(values, self.authentication.verify)
            if self.policy is not None:
                self.policy.check(principal)

        if authorized:

            async def unary(request: object, context: grpc.aio.ServicerContext) -> object:
                try:
                    recheck()
                    response = await handler.unary_unary(request, context)
                    recheck()
                    return response
                except Failure:
                    await context.abort(grpc.StatusCode.PERMISSION_DENIED, "Client access denied")
                    return None

            async def stream(
                request: object, context: grpc.aio.ServicerContext
            ) -> AsyncIterator[object]:
                try:
                    recheck()
                    async for response in handler.unary_stream(request, context):
                        recheck()
                        yield response
                except Failure:
                    await context.abort(grpc.StatusCode.PERMISSION_DENIED, "Client access denied")

            factory = (
                grpc.unary_stream_rpc_method_handler
                if handler.response_streaming
                else grpc.unary_unary_rpc_method_handler
            )
            return factory(
                stream if handler.response_streaming else unary,
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )

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


class HttpContext(DefaultServerCallContextBuilder):
    def build(self, request: Request) -> ServerCallContext:
        context = super().build(request)
        context.state["headers"] = {
            key: value
            for key, value in context.state.get("headers", {}).items()
            if key.lower() != "authorization"
        }
        context.state["checkedflow.principal"] = request.scope.get("checkedflow.principal")
        return context


class GrpcContext(DefaultGrpcServerCallContextBuilder):
    def __init__(self, authentication: Authentication) -> None:
        self.authentication = authentication

    def build(self, context: grpc.aio.ServicerContext) -> ServerCallContext:
        result = super().build(context)
        values = [v for k, v in context.invocation_metadata() if k.lower() == "authorization"]
        result.state["checkedflow.principal"] = authenticated_headers(
            values, self.authentication.verify
        )
        return result


def application(
    gateway: Gateway,
    url: str,
    token: str,
    *,
    journal_path: Path | None = None,
    push_hosts: tuple[str, ...] = (),
    grpc_url: str = "",
    callback_keys: Keyring | None = None,
    policy: Policy | None = None,
    oauth: OAuth | None = None,
    artifacts: Reader | None = None,
    grpc_tls: MutualTLS | None = None,
    grpc_advertised_url: str = "",
) -> Guard:
    require(artifacts is None or artifacts.policy is policy, "ACCESS", "artifact policy differs")
    authentication = Authentication(token, oauth)
    require(
        not grpc_advertised_url or (bool(grpc_url) and grpc_tls is not None),
        "TLS",
        "secure gRPC required",
    )
    grpc_interface = (
        advertised(grpc_advertised_url, rpc=False)
        if grpc_advertised_url
        else f"https://{grpc_url}"
        if grpc_url and grpc_tls is not None
        else grpc_url
    )
    agent_card = card(url, grpc_interface)
    journal = Journal(gateway, journal_path, keyring=callback_keys)
    handler = Handler(
        gateway, journal=journal, agent_card=agent_card, push=Push(push_hosts), policy=policy
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        server = None
        try:
            if grpc_url:
                server = grpc.aio.server(
                    interceptors=[BearerInterceptor(token, oauth, policy)],
                    options=[
                        ("grpc.max_receive_message_length", MAX_BODY),
                        ("grpc.max_send_message_length", 16 * 1048576),
                    ],
                    maximum_concurrent_rpcs=128,
                )
                add_A2AServiceServicer_to_server(
                    GrpcHandler(handler, context_builder=GrpcContext(authentication)), server
                )  # type: ignore[no-untyped-call]
                bound = (
                    server.add_secure_port(grpc_url, grpc_tls.grpc_credentials())
                    if grpc_tls is not None
                    else server.add_insecure_port(grpc_url)
                )
                require(bound != 0, "ADDRESS", "gRPC bind failed")
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
        + create_jsonrpc_routes(
            handler, "/rpc", context_builder=HttpContext(), enable_v0_3_compat=False
        )
        + create_rest_routes(
            handler, path_prefix="/v1", context_builder=HttpContext(), enable_v0_3_compat=False
        )
    )
    app = Starlette(routes=routes, lifespan=lifespan)
    return Guard(
        Downloads(app, artifacts) if artifacts is not None else app,
        None,
        authenticate=authentication.verify,
        policy=policy,
    )


def serve(
    gateway: Gateway,
    host: str,
    port: int,
    token: str,
    *,
    journal_path: Path,
    push_hosts: tuple[str, ...] = (),
    grpc_port: int = 0,
    callback_keys: Keyring | None = None,
    policy: Policy | None = None,
    oauth: OAuth | None = None,
    artifacts: Reader | None = None,
    tls: MutualTLS | None = None,
    advertised_url: str = "",
    grpc_advertised_url: str = "",
) -> None:
    require(ipaddress.ip_address(host).is_loopback, "ADDRESS", "bind a numeric loopback address")
    require(
        1 <= port <= 65535 and 0 <= grpc_port <= 65535 and grpc_port != port,
        "ADDRESS",
        "invalid or conflicting port",
    )
    address = f"[{host}]" if ":" in host else host
    require(not advertised_url or tls is not None, "TLS", "proxy deployment requires mutual TLS")
    url = (
        advertised(advertised_url, rpc=True)
        if advertised_url
        else f"{'https' if tls else 'http'}://{address}:{port}/rpc"
    )
    app = application(
        gateway,
        url,
        token,
        journal_path=journal_path,
        push_hosts=push_hosts,
        grpc_url=f"{address}:{grpc_port}" if grpc_port else "",
        callback_keys=callback_keys,
        policy=policy,
        oauth=oauth,
        artifacts=artifacts,
        grpc_tls=tls,
        grpc_advertised_url=grpc_advertised_url,
    )
    asyncio.run(uvicorn.Server(http_config(app, host, port, tls)).serve())
