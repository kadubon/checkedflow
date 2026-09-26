"""Scoped S3 bytes with explicit SigV4 credentials and verified conditional publication.

No bucket provisioning, listing, presigned URLs or ambient credential discovery.
Physical erasure requires separate privileged access and the owning retention controller.
Service compatibility and retention are separate from a successful point-in-time read.
"""

import re
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

import httpx
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials as AwsCredentials

from checkedflow.artifact_io import Access, read_verified, verify
from checkedflow.core.artifact import Reference
from checkedflow.core.values import Failure, require


@dataclass(frozen=True)
class Credentials:
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        require(
            isinstance(self.access_key, str) and isinstance(self.secret_key, str),
            "CREDENTIAL",
            "explicit S3 access and secret keys required",
        )
        for value, maximum in (
            (self.access_key, 128),
            (self.secret_key, 256),
            (self.session_token, 4096),
        ):
            require(
                value is None
                or (
                    isinstance(value, str)
                    and 0 < len(value) <= maximum
                    and all(33 <= ord(character) <= 126 for character in value)
                ),
                "CREDENTIAL",
                "explicit printable S3 credentials required",
            )


class S3Store:
    def __init__(
        self,
        endpoint: str,
        bucket: str,
        credentials: Credentials,
        *,
        region: str = "us-east-1",
        prefix: str = "checkedflow",
        ca_file: Path | None = None,
        timeout: float = 10,
        allow_loopback_http: bool = False,
    ) -> None:
        require(
            type(allow_loopback_http) is bool, "TLS", "explicit boolean loopback policy required"
        )
        try:
            parsed = urlsplit(endpoint)
            port = parsed.port
        except ValueError:
            raise Failure("ENDPOINT", "invalid configured S3 origin") from None
        require(
            parsed.scheme in {"https", "http"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {"", "/"}
            and endpoint.isascii()
            and not any(c.isspace() for c in endpoint)
            and "%" not in parsed.netloc
            and (port is None or 1 <= port <= 65535),
            "ENDPOINT",
            "fixed S3 origin without credentials, path or query required",
        )
        require(
            parsed.scheme == "https" or (allow_loopback_http and parsed.hostname == "127.0.0.1"),
            "TLS",
            "S3 requires HTTPS except explicit literal-loopback tests",
        )
        require(
            re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket) is not None,
            "SCOPE",
            "portable bucket name required",
        )
        require(
            re.fullmatch(r"[a-z0-9-]{1,64}", prefix) is not None
            and re.fullmatch(r"[a-z0-9-]{1,64}", region) is not None,
            "SCOPE",
            "portable prefix and explicit region required",
        )
        require(
            type(timeout) in {int, float} and 0 < timeout <= 30,
            "TIMEOUT",
            "bounded S3 I/O timeout required",
        )
        try:
            self._tls = ssl.create_default_context(cafile=ca_file)
        except (OSError, ssl.SSLError):
            raise Failure("TLS", "invalid configured CA material") from None
        self.endpoint = endpoint.rstrip("/")
        self.bucket, self.prefix, self.region, self.timeout = bucket, prefix, region, timeout
        self._credentials = AwsCredentials(
            credentials.access_key, credentials.secret_key, credentials.session_token
        )

    def _url(self, ref: Reference) -> str:
        return f"{self.endpoint}/{self.bucket}/{self.prefix}/{ref.scope}/{ref.digest}"

    def _request(self, method: str, ref: Reference, body: bytes = b"") -> tuple[int, bytes]:
        headers = {"Accept-Encoding": "identity"}
        if method == "PUT":
            headers.update({"If-None-Match": "*", "Content-Type": ref.content_type})
        request = AWSRequest(method=method, url=self._url(ref), data=body, headers=headers)
        S3SigV4Auth(self._credentials, "s3", self.region).add_auth(request)
        signed_headers: dict[str, str] = dict(request.headers.items())
        started = time.monotonic()
        try:
            with (
                httpx.Client(
                    verify=self._tls, timeout=self.timeout, trust_env=False, follow_redirects=False
                ) as client,
                client.stream(
                    method, self._url(ref), content=body, headers=signed_headers
                ) as response,
            ):
                require(
                    time.monotonic() - started <= self.timeout,
                    "TRANSPORT",
                    "S3 response deadline exceeded",
                )
                code = response.status_code
                if code != 200:
                    return code, b""
                require(
                    response.headers.get("content-encoding", "identity") == "identity",
                    "TRANSPORT",
                    "compressed S3 response rejected",
                )
                limit = ref.length if method == "GET" else 4096
                length = response.headers.get("content-length")
                require(
                    length is None
                    or (
                        len(length) <= 12
                        and length.isascii()
                        and length.isdecimal()
                        and int(length) <= limit
                    ),
                    "INTEGRITY",
                    "S3 response length exceeds reference",
                )
                data = bytearray()
                # No chunk_size: enforce the elapsed bound after each transport chunk.
                for part in response.iter_raw():
                    require(
                        time.monotonic() - started <= self.timeout,
                        "TRANSPORT",
                        "S3 stream deadline exceeded",
                    )
                    require(len(data) + len(part) <= limit, "INTEGRITY", "S3 response byte ceiling")
                    data.extend(part)
                return code, bytes(data)
        except (httpx.HTTPError, OSError):
            raise Failure(
                "TRANSPORT", "S3 request failed; no endpoint or credentials disclosed"
            ) from None

    def _get(self, ref: Reference) -> bytes:
        code, body = self._request("GET", ref)
        require(code not in {401, 403}, "AUTHORITY", "S3 service denied access")
        require(code == 200, "UNAVAILABLE", "S3 object unavailable")
        verify(ref, body)
        return body

    def get(self, ref: Reference, *, access: Access) -> bytes:
        access.authorize(ref.scope, "read")
        return self._get(ref)

    def erase(self, ref: Reference, *, access: Access) -> None:
        """One privileged DELETE; confirm absence without automatically repeating the effect."""
        access.authorize(ref.scope, "erase")
        try:
            code, _ = self._request("DELETE", ref)
            require(code not in {401, 403}, "AUTHORITY", "S3 service denied erasure")
            require(code in {200, 204, 404}, "OUTCOME_UNKNOWN", "S3 erasure unconfirmed")
            code, _ = self._request("GET", ref)
            require(code == 404, "OUTCOME_UNKNOWN", "S3 absence unconfirmed")
        except Failure as failure:
            if failure.code == "AUTHORITY":
                raise
            raise Failure("OUTCOME_UNKNOWN", "S3 erasure requires reconciliation") from None

    def put(self, ref: Reference, source: BinaryIO, *, access: Access) -> None:
        access.authorize(ref.scope, "write")
        body = read_verified(ref, source)
        ambiguous = False
        try:
            code, _ = self._request("PUT", ref, body)
        except Failure:
            ambiguous = True
            code = 0
        require(code not in {401, 403}, "AUTHORITY", "S3 service denied publication")
        # Never repeat PUT after an ambiguous response. One verified read can reconcile it.
        try:
            if code not in {200, 409, 412} and not ambiguous:
                raise Failure("OUTCOME_UNKNOWN", "S3 publication response unconfirmed")
            existing = self._get(ref)
            require(existing == body, "INTEGRITY", "existing S3 object conflicts with input")
        except Failure as failure:
            if failure.code == "INTEGRITY":
                raise
            raise Failure(
                "OUTCOME_UNKNOWN", "S3 publication unconfirmed; reconcile by verified read"
            ) from None
