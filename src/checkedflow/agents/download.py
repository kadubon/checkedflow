"""Read-only publication of explicitly catalogued bytes under current client policy.

The catalog is operator-owned authority, not a client assertion or a consensus proof.
It binds complete references to one chain/mission; knowing a digest grants no access.
"""

from pathlib import Path
from typing import cast

import anyio
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from checkedflow.agents.access import Policy, Principal
from checkedflow.artifact_io import Access, ArtifactStore, verify
from checkedflow.core.artifact import Reference, reference
from checkedflow.core.values import Failure, array, fields, obj, require
from checkedflow.wire import document


class Reader:
    """An explicit publication allowlist, independent of storage provider and transport."""

    def __init__(self, store: ArtifactStore, catalog: Path, policy: Policy) -> None:
        self.store, self.catalog, self.policy = store, catalog, policy
        self._load()

    def _load(self) -> dict[str, Reference]:
        try:
            with self.catalog.open("rb") as source:
                raw = source.read(262145)
            require(len(raw) <= 262144, "ACCESS", "publication catalog size limit")
            value = document(raw)
            fields(value, "profile chain mission artifacts")
            require(
                value["profile"] == "checkedflow/artifact-publication/v1"
                and value["chain"] == self.policy.chain
                and value["mission"] == self.policy.mission,
                "ACCESS",
                "publication catalog binding differs",
            )
            result: dict[str, Reference] = {}
            for item in array(value["artifacts"], limit=256):
                ref = reference(obj(item))
                require(ref.scope == self.policy.mission, "ACCESS", "publication scope differs")
                require(ref.digest not in result, "ACCESS", "duplicate published digest")
                result[ref.digest] = ref
            return result
        except (ValueError, OSError) as exc:
            raise Failure("ACCESS", "publication catalog unavailable or invalid") from exc

    def read(self, principal: Principal | None, fingerprint: str) -> tuple[Reference, bytes]:
        self.policy.check(principal)
        ref = self._load().get(fingerprint)
        if ref is None:
            raise Failure("NOT_FOUND", "artifact not published in this mission")
        access = Access(
            cast(Principal, principal).identity, frozenset({ref.scope}), frozenset({"read"})
        )
        body = self.store.get(ref, access=access)
        verify(ref, body)  # A provider cannot bypass the public boundary's integrity check.
        self.policy.check(principal)
        require(self._load().get(fingerprint) == ref, "ACCESS", "publication changed during read")
        return ref, body


class Downloads:
    """Shared HTTP extension, mounted inside the transport's authenticated Guard."""

    def __init__(self, app: ASGIApp, reader: Reader) -> None:
        self.app, self.reader = app, reader

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/artifacts/"):
            await self.app(scope, receive, send)
            return
        response: Response
        if scope["method"] != "GET" or scope.get("query_string"):
            response = JSONResponse({"error": "REQUEST"}, status_code=400)
        else:
            owner = scope.get("checkedflow.principal")
            try:
                ref, body = await anyio.to_thread.run_sync(
                    self.reader.read,
                    owner if isinstance(owner, Principal) else None,
                    scope["path"][len("/artifacts/") :],
                )
                response = Response(
                    body,
                    media_type=ref.content_type,
                    headers={
                        "Cache-Control": "no-store",
                        "X-Content-Type-Options": "nosniff",
                        "Content-Disposition": f'attachment; filename="{ref.digest}"',
                    },
                )
            except Failure as exc:
                status = 403 if exc.code == "ACCESS" else 404 if exc.code == "NOT_FOUND" else 503
                response = JSONResponse({"error": exc.code}, status_code=status)
        await response(scope, receive, send)
