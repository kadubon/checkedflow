"""Shared bounded HTTP admission for locally hosted agent transports."""

import hmac
import json
import math
from collections.abc import Callable

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from checkedflow.agents.access import Policy, Principal
from checkedflow.agents.authentication import authenticated_headers
from checkedflow.core.values import Failure, require

MAX_BODY = 8 * 1048576


def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def validate_json(raw: bytes) -> None:
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 64 or (isinstance(item, float) and not math.isfinite(item)):
            raise ValueError("JSON bound")
        if isinstance(item, str):
            item.encode("utf-8")
        if isinstance(item, dict):
            for key in item:
                key.encode("utf-8")
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)


class Guard:
    def __init__(
        self,
        app: ASGIApp,
        token: str | None,
        *,
        public: tuple[str, ...] = ("/.well-known/agent-card.json",),
        origins: tuple[str, ...] = (),
        authenticate: Callable[[str], Principal | None] | None = None,
        policy: Policy | None = None,
        challenge: str = "Bearer",
    ) -> None:
        require(
            token is None or (len(token) >= 32 and all(33 <= ord(c) <= 126 for c in token)),
            "AUTH",
            "use a printable ASCII token of 32+ characters without whitespace",
        )
        # None is for an app that supplies the SDK's OAuth resource-server middleware.
        self.app, self.expected = app, ("Bearer " + token).encode() if token is not None else None
        require(
            policy is None or authenticate is not None, "ACCESS", "policy requires authentication"
        )
        self.public, self.origins = public, origins
        self.authenticate, self.policy = authenticate, policy
        self.challenge = challenge

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        origin = request.headers.get("origin")
        if origin and origin not in self.origins:
            await JSONResponse({"error": "origin rejected"}, status_code=403)(scope, receive, send)
            return
        if scope["path"] in self.public and scope["method"] == "GET":
            await self.app(scope, receive, send)
            return
        credentials = request.headers.getlist("authorization")
        if self.expected is not None and (
            len(credentials) != 1 or not hmac.compare_digest(credentials[0].encode(), self.expected)
        ):
            await JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": self.challenge},
            )(scope, receive, send)
            return
        principal = None
        if self.authenticate is not None:
            try:
                principal = await anyio.to_thread.run_sync(
                    authenticated_headers, credentials, self.authenticate
                )
            except Failure:
                await JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": self.challenge},
                )(scope, receive, send)
                return
            scope["checkedflow.principal"] = principal
        if self.policy is not None:
            try:
                await anyio.to_thread.run_sync(self.policy.check, principal)
            except Failure:
                await JSONResponse({"error": "forbidden"}, status_code=403)(scope, receive, send)
                return
        body = bytearray()
        try:
            with anyio.fail_after(30):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY:
                        await JSONResponse({"error": "body limit"}, status_code=413)(
                            scope, receive, send
                        )
                        return
                    body.extend(chunk)
            if body:
                validate_json(bytes(body))
        except TimeoutError:
            await JSONResponse({"error": "request timeout"}, status_code=408)(scope, receive, send)
            return
        except (ValueError, RecursionError):
            await JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Invalid JSON"},
                },
                status_code=400,
            )(scope, receive, send)
            return
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        started = False

        class Revoked(Exception):
            pass

        async def authorized_send(message: Message) -> None:
            nonlocal started
            if self.policy is not None or self.authenticate is not None:
                try:
                    if self.authenticate is not None:
                        await anyio.to_thread.run_sync(
                            authenticated_headers, credentials, self.authenticate
                        )
                    if self.policy is not None:
                        await anyio.to_thread.run_sync(self.policy.check, principal)
                except Failure:
                    if not started:
                        await JSONResponse({"error": "forbidden"}, status_code=403)(
                            scope, replay, send
                        )
                    else:
                        await send({"type": "http.response.body", "body": b"", "more_body": False})
                    raise Revoked() from None
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, replay, authorized_send)
        except Revoked:
            return
