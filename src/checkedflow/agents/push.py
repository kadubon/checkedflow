"""Bounded push delivery to operator-allowlisted HTTPS origins, with DNS pinning."""

import ipaddress
import socket
from urllib.parse import urlsplit

import anyio
import httpx

from checkedflow.core.values import Object, obj, require, text
from checkedflow.wire import dumps


class Push:
    def __init__(self, hosts: tuple[str, ...] = ()) -> None:
        self.hosts = frozenset(host.lower() for host in hosts)

    def validate(self, configuration: Object) -> str:
        url = text(configuration.get("url"), limit=2048)
        parsed = urlsplit(url)
        require(
            parsed.scheme == "https"
            and parsed.hostname in self.hosts
            and parsed.port in {None, 443}
            and not parsed.username
            and not parsed.password
            and not parsed.fragment,
            "PUSH_ORIGIN",
            "callback must use an operator-allowlisted HTTPS host on port 443",
        )
        auth = obj(configuration.get("authentication", {}))
        scheme = auth.get("scheme", "")
        require(
            set(auth) <= {"scheme", "credentials"}
            and isinstance(scheme, str)
            and (not auth or scheme.lower() == "bearer"),
            "PUSH_AUTH",
            "Bearer authentication supported",
        )
        for value in (configuration.get("token", ""), auth.get("credentials", "")):
            require(
                isinstance(value, str)
                and len(value) <= 4096
                and all(32 <= ord(c) < 127 for c in value),
                "PUSH_AUTH",
                "invalid header value",
            )
        return url

    async def deliver(self, configuration: Object, task: Object) -> bool:
        url = self.validate(configuration)
        host = urlsplit(url).hostname or ""
        records = await anyio.to_thread.run_sync(
            lambda: socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        )
        addresses = {str(record[4][0]) for record in records}
        require(
            bool(addresses) and all(ipaddress.ip_address(ip).is_global for ip in addresses),
            "PUSH_ADDRESS",
            "callback DNS must resolve only to public addresses",
        )
        # Connect to the inspected IP, while validating TLS against the original hostname.
        target = httpx.URL(url).copy_with(host=sorted(addresses)[0])
        headers = {"Host": host, "Content-Type": "application/a2a+json"}
        if configuration.get("token"):
            headers["X-A2A-Notification-Token"] = str(configuration["token"])
        auth = obj(configuration.get("authentication", {}))
        if auth.get("credentials"):
            headers["Authorization"] = "Bearer " + str(auth["credentials"])
        async with (
            httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client,
            client.stream(
                "POST",
                target,
                content=dumps({"task": task}, string_limit=16777216),
                headers=headers,
                extensions={"sni_hostname": host.encode()},
            ) as response,
        ):
            # Do not read an untrusted or unbounded response body, and never follow redirects.
            return 200 <= response.status_code < 300
