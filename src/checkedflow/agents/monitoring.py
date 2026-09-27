"""Opt-in status routes, mounted only inside the authenticated mission-policy guard."""

import anyio
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from checkedflow.observability import Monitor


class Monitoring:
    def __init__(self, app: ASGIApp, monitor: Monitor) -> None:
        self.app, self.monitor = app, monitor

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path")
        if scope["type"] != "http" or path not in {"/healthz", "/readyz", "/metrics"}:
            await self.app(scope, receive, send)
            return
        if scope["method"] != "GET":
            await JSONResponse({"error": "method not allowed"}, status_code=405)(
                scope, receive, send
            )
            return
        if path == "/healthz":
            await JSONResponse({"live": True, "role": self.monitor.role})(scope, receive, send)
            return
        observed = await anyio.to_thread.run_sync(self.monitor.observe)
        if path == "/metrics":
            await PlainTextResponse(observed.prometheus(), media_type="text/plain; version=0.0.4")(
                scope, receive, send
            )
        else:
            await JSONResponse(observed.record(), status_code=200 if observed.ready else 503)(
                scope, receive, send
            )
