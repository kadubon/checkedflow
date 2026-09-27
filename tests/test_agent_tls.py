"""Actual HTTP/gRPC mutual TLS handshakes, independent of bearer and mission authority."""

import asyncio
import ipaddress
import socket
import ssl
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import grpc
import httpx
import pytest
import uvicorn
from a2a.types import a2a_pb2 as pb
from a2a.types import a2a_pb2_grpc as pb_grpc
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from starlette.responses import JSONResponse
from test_agent_access import grant, save
from test_agents import TOKEN
from test_operational_gateway import Node

from checkedflow.agents.a2a import create_app
from checkedflow.agents.access import Policy
from checkedflow.agents.mcp import create_http_app
from checkedflow.agents.tls import MutualTLS, advertised, http_config, pem
from checkedflow.core.values import Failure


def material(path, *, ca=None, purpose=None, expired=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, path.name)])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca[1].subject if ca else name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=-1 if expired else 1))
        .add_extension(x509.BasicConstraints(ca=ca is None, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca[0].public_key() if ca else key.public_key()
            ),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca is None,
                crl_sign=ca is None,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    if purpose is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage([purpose]), critical=False)
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.DNSName("localhost"),
                ]
            ),
            critical=False,
        )
    cert = builder.sign(ca[0] if ca else key, hashes.SHA256())
    path.with_suffix(".pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    path.with_suffix(".key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return key, cert


@pytest.fixture
def certificates(tmp_path):
    ca = material(tmp_path / "ca")
    material(tmp_path / "server", ca=ca, purpose=ExtendedKeyUsageOID.SERVER_AUTH)
    material(tmp_path / "client", ca=ca, purpose=ExtendedKeyUsageOID.CLIENT_AUTH)
    material(tmp_path / "expired", ca=ca, purpose=ExtendedKeyUsageOID.CLIENT_AUTH, expired=True)
    foreign = material(tmp_path / "foreign-ca")
    material(tmp_path / "foreign", ca=foreign, purpose=ExtendedKeyUsageOID.CLIENT_AUTH)
    return tmp_path


def settings(root):
    return MutualTLS(root / "server.pem", root / "server.key", root / "ca.pem")


def client_context(root, client="client", trust="ca"):
    context = ssl.create_default_context(cafile=root / (trust + ".pem"))
    if client:
        context.load_cert_chain(root / (client + ".pem"), root / (client + ".key"))
    return context


@asynccontextmanager
async def secured(app, tls):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    class Server(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            yield

    config = http_config(app, "127.0.0.1", port, tls)
    config.log_level = "error"
    server = Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(500):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert server.started
        yield f"https://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        sock.close()


@pytest.mark.qualification
@pytest.mark.parametrize("transport", ["a2a", "mcp"])
def test_http_mtls_does_not_replace_bearer_or_client_policy(certificates, transport):
    root = certificates
    save(root / "policy.json", [grant()])
    policy = Policy(root / "policy.json", "operational-test", "m")
    app = (
        create_app(Node().gateway(), "https://localhost/rpc", TOKEN, policy=policy)
        if transport == "a2a"
        else create_http_app(Node().gateway(), TOKEN, policy=policy)
    )
    path = "/v1/tasks" if transport == "a2a" else "/mcp"

    async def run():
        async with secured(app, settings(root)) as url:
            with pytest.raises(ssl.SSLCertVerificationError):
                await asyncio.open_connection(
                    "127.0.0.1",
                    urlsplit(url).port,
                    ssl=client_context(root),
                    server_hostname="wrong.example",
                )
            for client, trust in (
                ("", "ca"),
                ("foreign", "ca"),
                ("expired", "ca"),
                ("client", "foreign-ca"),
            ):
                async with httpx.AsyncClient(
                    verify=client_context(root, client, trust), timeout=2, trust_env=False
                ) as http:
                    with pytest.raises(httpx.TransportError):
                        await http.get(url + path)
            async with httpx.AsyncClient(
                verify=client_context(root), timeout=3, trust_env=False
            ) as http:
                assert (
                    await http.get(
                        url + path,
                        headers={"X-Client-Cert": "operator", "X-Forwarded-User": "operator"},
                    )
                ).status_code == 401
                headers = {"Authorization": "Bearer " + TOKEN, "A2A-Version": "1.0"}
                if transport == "a2a":
                    assert (await http.get(url + path, headers=headers)).status_code == 200
                else:
                    # Actual MCP initialize, without relying on a malformed request as success.
                    headers["Accept"] = "application/json, text/event-stream"
                    response = await http.post(
                        url + path,
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-11-25",
                                "capabilities": {},
                                "clientInfo": {"name": "tls-test", "version": "1"},
                            },
                        },
                    )
                    assert response.status_code == 200
                    assert "serverInfo" in response.text
                save(policy.path, [])
                assert (await http.get(url + path, headers=headers)).status_code == 403

    asyncio.run(run())


@pytest.mark.qualification
def test_actual_grpc_requires_certificate_token_and_current_grant(certificates):
    root = certificates
    save(root / "policy.json", [grant()])
    policy = Policy(root / "policy.json", "operational-test", "m")
    node = Node()
    tls = settings(root)

    async def run():
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        address = f"127.0.0.1:{port}"
        app = create_app(
            node.gateway(),
            "https://localhost/rpc",
            TOKEN,
            policy=policy,
            grpc_url=address,
            grpc_tls=tls,
        )
        async with secured(app, tls) as url:
            async with httpx.AsyncClient(verify=client_context(root), trust_env=False) as http:
                card = (await http.get(url + "/.well-known/agent-card.json")).json()
                assert card["supportedInterfaces"][-1]["url"] == "https://" + address
            metadata = (("authorization", "Bearer " + TOKEN), ("a2a-version", "1.0"))
            for client in (None, "foreign", "expired", "client"):
                credentials = grpc.ssl_channel_credentials(
                    root_certificates=pem(root / "ca.pem"),
                    private_key=pem(root / (client + ".key")) if client else None,
                    certificate_chain=pem(root / (client + ".pem")) if client else None,
                )
                async with grpc.aio.secure_channel(address, credentials) as channel:
                    stub = pb_grpc.A2AServiceStub(channel)
                    if client != "client":
                        with pytest.raises(grpc.aio.AioRpcError) as caught:
                            await stub.GetTask(
                                pb.GetTaskRequest(id=node.task), metadata=metadata, timeout=2
                            )
                        assert caught.value.code() in {
                            grpc.StatusCode.UNAVAILABLE,
                            grpc.StatusCode.DEADLINE_EXCEEDED,
                        }
                        continue
                    with pytest.raises(grpc.aio.AioRpcError) as caught:
                        await stub.GetTask(pb.GetTaskRequest(id=node.task), timeout=2)
                    assert caught.value.code() == grpc.StatusCode.UNAUTHENTICATED
                    assert (
                        await stub.GetTask(
                            pb.GetTaskRequest(id=node.task), metadata=metadata, timeout=2
                        )
                    ).id == node.task
                    save(policy.path, [])
                    with pytest.raises(grpc.aio.AioRpcError) as caught:
                        await stub.GetTask(
                            pb.GetTaskRequest(id=node.task), metadata=metadata, timeout=2
                        )
                    assert caught.value.code() == grpc.StatusCode.PERMISSION_DENIED

    asyncio.run(run())


@pytest.mark.qualification
def test_forwarded_headers_cannot_override_transport(certificates, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")

    async def app(scope, receive, send):
        if scope["type"] == "http":
            await JSONResponse({"scheme": scope["scheme"], "client": scope["client"][0]})(
                scope, receive, send
            )
        else:
            while True:
                event = await receive()
                await send({"type": event["type"] + ".complete"})
                if event["type"] == "lifespan.shutdown":
                    break

    async def run():
        async with (
            secured(app, settings(certificates)) as url,
            httpx.AsyncClient(verify=client_context(certificates), trust_env=False) as http,
        ):
            response = await http.get(
                url, headers={"X-Forwarded-Proto": "http", "X-Forwarded-For": "198.51.100.1"}
            )
            assert response.json() == {"scheme": "https", "client": "127.0.0.1"}

    asyncio.run(run())


@pytest.mark.qualification
def test_client_ca_replacement_requires_new_listener_and_rejects_retired_ca(certificates):
    root = certificates

    async def app(scope, receive, send):
        if scope["type"] == "http":
            await JSONResponse({"ok": True})(scope, receive, send)
        else:
            while True:
                event = await receive()
                await send({"type": event["type"] + ".complete"})
                if event["type"] == "lifespan.shutdown":
                    return

    async def run():
        first = settings(root)
        second = MutualTLS(root / "server.pem", root / "server.key", root / "foreign-ca.pem")
        for tls, accepted, rejected in (
            (first, "client", "foreign"),
            (second, "foreign", "client"),
        ):
            async with secured(app, tls) as url:
                async with httpx.AsyncClient(
                    verify=client_context(root, accepted), trust_env=False
                ) as http:
                    assert (await http.get(url)).status_code == 200
                async with httpx.AsyncClient(
                    verify=client_context(root, rejected), trust_env=False
                ) as http:
                    with pytest.raises(httpx.TransportError):
                        await http.get(url)

    asyncio.run(run())


@pytest.mark.parametrize(
    "flags",
    [
        ["--tls-cert-file", "missing"],
        ["--tls-cert-file", "missing", "--tls-key-file", "missing", "--tls-client-ca", "missing"],
    ],
)
def test_cli_rejects_incomplete_tls_or_tls_on_stdio_before_rpc(tmp_path, capfd, flags):
    import json

    from checkedflow.cli import main

    save(tmp_path / "policy.json", [grant()])
    assert (
        main(
            [
                "mcp",
                "--protocol",
                "v2",
                "--chain",
                "operational-test",
                "--mission",
                "m",
                "--rpc",
                "http://127.0.0.1:1",
                "--access-policy",
                str(tmp_path / "policy.json"),
                *flags,
            ]
        )
        == 2
    )
    assert json.loads(capfd.readouterr().err)["error"] == "TLS"


def test_advertised_grpc_requires_secure_configured_listener():
    with pytest.raises(Failure, match="TLS"):
        create_app(
            Node().gateway(),
            "https://localhost/rpc",
            TOKEN,
            grpc_advertised_url="https://agent.example:8444",
        )


@pytest.mark.parametrize(
    "kind", ["missing", "large", "empty", "invalid", "mismatch", "non-ascii", "encrypted"]
)
def test_tls_material_fails_closed(certificates, kind):
    root = certificates
    if kind == "missing":
        (root / "server.key").unlink()
    elif kind == "encrypted":
        key = serialization.load_pem_private_key(pem(root / "server.key"), password=None)
        (root / "server.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(b"test-only-encryption"),
            )
        )
    elif kind == "mismatch":
        (root / "server.key").write_bytes(pem(root / "client.key"))
    else:
        (root / "ca.pem").write_bytes(
            {"large": b"a" * 65537, "empty": b"", "invalid": b"invalid", "non-ascii": b"\xff"}[kind]
        )
    with pytest.raises(Failure, match="TLS") as caught:
        settings(root)
    assert str(root) not in str(caught.value)


def test_tls_snapshot_and_minimum_version(certificates, monkeypatch):
    tls = settings(certificates)
    config = http_config(None, "127.0.0.1", 0, tls)
    assert config.is_ssl and not config.proxy_headers
    context = tls.factory(config, lambda: None)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert not http_config(None, "127.0.0.1", 0).is_ssl
    original = ssl.SSLContext.load_cert_chain

    def changed(context, *args, **kwargs):
        original(context, *args, **kwargs)
        (certificates / "ca.pem").write_bytes(pem(certificates / "foreign-ca.pem"))

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", changed)
    with pytest.raises(Failure, match="TLS"):
        settings(certificates)


@pytest.mark.parametrize(
    "url",
    [
        "http://host/rpc",
        "https://u:p@host/rpc",
        "https://host/rpc?x=1",
        "https://host/rpc#x",
        "https://host:bad/rpc",
        "https://host:0/rpc",
        "https://host/other",
        "https://host/rpc\n",
        "https:///rpc",
        "https://host\\path/rpc",
        "https://" + "a" * 2048 + "/rpc",
    ],
)
def test_advertised_proxy_url_is_explicit_and_bounded(url):
    with pytest.raises(Failure, match="TLS"):
        advertised(url, rpc=True)
    assert advertised("https://agent.example:8443/rpc", rpc=True)
    assert advertised("https://agent.example:8444", rpc=False)
