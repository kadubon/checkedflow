"""Operator-provisioned mutual TLS, separate from bearer identity and command authority."""

import ssl
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import grpc
import uvicorn
from starlette.types import ASGIApp

from checkedflow.core.values import Failure, require


def pem(path: Path) -> bytes:
    with path.open("rb") as source:
        value = source.read(65537)
    require(0 < len(value) <= 65536, "TLS", "certificate material size limit")
    return value


def reject_password() -> str:
    """Never let OpenSSL prompt for a password in a supervised server process."""
    raise Failure("TLS", "password-encrypted server keys require an external key mechanism")


class MutualTLS:
    """One startup snapshot. Rotation requires draining and restarting every listener.

    No default CA, generated key, insecure fallback, or certificate-to-role inference.
    Files must be protected against candidate processes and untrusted local writers.
    """

    def __init__(self, certificate: Path, key: Path, client_ca: Path) -> None:
        try:
            self._certificate, self._key, self._ca = (pem(p) for p in (certificate, key, client_ca))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_verify_locations(cadata=self._ca.decode("ascii"))
            context.load_cert_chain(certificate, key, password=reject_password)
            require(
                (pem(certificate), pem(key), pem(client_ca))
                == (self._certificate, self._key, self._ca),
                "TLS",
                "certificate material changed during startup",
            )
        except (OSError, ValueError) as exc:
            raise Failure("TLS", "mutual TLS configuration unavailable or invalid") from exc
        self._context = context

    def factory(
        self, config: uvicorn.Config, default: Callable[[], ssl.SSLContext]
    ) -> ssl.SSLContext:
        return self._context

    def grpc_credentials(self) -> grpc.ServerCredentials:
        return grpc.ssl_server_credentials(
            [(self._key, self._certificate)], root_certificates=self._ca, require_client_auth=True
        )


def http_config(app: ASGIApp, host: str, port: int, tls: MutualTLS | None = None) -> uvicorn.Config:
    """Shared server settings; forwarded headers never assert identity, scheme or client IP."""
    return uvicorn.Config(
        app,
        host=host,
        port=port,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        limit_concurrency=128,
        timeout_graceful_shutdown=10,
        ssl_context_factory=tls.factory if tls is not None else None,
    )


def advertised(url: str, *, rpc: bool) -> str:
    """Validate an explicit operator-owned proxy URL; never infer it from request headers."""
    try:
        value = urlsplit(url)
        port = value.port
        require(
            len(url) <= 2048
            and "\\" not in url
            and value.scheme == "https"
            and bool(value.hostname)
            and value.username is None
            and value.password is None
            and not value.query
            and not value.fragment
            and value.path == ("/rpc" if rpc else "")
            and (port is None or 1 <= port <= 65535)
            and all(33 <= ord(c) <= 126 for c in url),
            "TLS",
            "explicit HTTPS proxy URL required",
        )
    except ValueError as exc:
        raise Failure("TLS", "invalid advertised HTTPS URL") from exc
    return url
