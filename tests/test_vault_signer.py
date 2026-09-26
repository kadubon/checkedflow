"""Bounded protocol-server tests; the separate real Vault gate is not replaced by these."""

import base64
import ipaddress
import json
import ssl
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID

from checkedflow.core.authority import Credential
from checkedflow.core.values import Failure
from checkedflow.operational_identity import DOMAIN
from checkedflow.vault_signer import MAX_RESPONSE, VaultSigner

MESSAGE = DOMAIN + b'{"example":"exact bytes, not a digest"}'


@contextmanager
def service(tmp_path, *, tls=False):
    key = ed25519.Ed25519PrivateKey.generate()
    state = SimpleNamespace(
        key=key,
        requests=[],
        status=200,
        delay=0,
        encoding=None,
        raw=None,
        signature=None,
        declared_length=None,
        metadata={
            "type": "ed25519",
            "derived": False,
            "exportable": False,
            "allow_plaintext_backup": False,
            "supports_signing": True,
            "keys": {
                "1": {"public_key": base64.b64encode(key.public_key().public_bytes_raw()).decode()}
            },
        },
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length)) if length else None
            state.requests.append(
                (self.command, self.path, self.headers.get("X-Vault-Token"), payload)
            )
            time.sleep(state.delay)
            if payload is None:
                result = state.metadata
            else:
                assert payload["key_version"] == 1 and payload["prehashed"] is False
                signature = state.signature
                if signature is None:
                    signature = (
                        "vault:v1:"
                        + base64.b64encode(key.sign(base64.b64decode(payload["input"]))).decode()
                    )
                result = {"signature": signature}
            body = state.raw if state.raw is not None else json.dumps({"data": result}).encode()
            try:
                self.send_response(state.status)
                self.send_header(
                    "Content-Length",
                    str(len(body) if state.declared_length is None else state.declared_length),
                )
                self.send_header("Location", "https://must-not-follow.invalid/")
                if state.encoding:
                    self.send_header("Content-Encoding", state.encoding)
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

        do_GET = respond
        do_POST = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if tls:
        ca_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "disposable test CA")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        state.ca = tmp_path / "test-ca.crt"
        state.ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path = tmp_path / "test-server-key"
        key_path.write_bytes(
            ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(state.ca, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    state.endpoint = f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}"
    state.credential = Credential(
        "operator", 1, "a", "administrator", "", key.public_key().public_bytes_raw().hex(), 0
    )
    state.signer = VaultSigner(
        state.credential,
        state.endpoint,
        "signing",
        1,
        "fixture-token",
        allow_insecure_loopback=not tls,
    )
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_exact_bytes_pinned_version_and_secret_repr(tmp_path, monkeypatch):
    with service(tmp_path) as instance:
        monkeypatch.setenv("HTTP_PROXY", "http://untrusted-proxy.invalid:9")
        assert instance.signer.sign(MESSAGE) == instance.key.sign(MESSAGE)
        assert [record[1] for record in instance.requests] == [
            "/v1/transit/keys/signing",
            "/v1/transit/sign/signing",
        ]
        assert all(record[2] == "fixture-token" for record in instance.requests)
        assert base64.b64decode(instance.requests[-1][3]["input"]) == MESSAGE
        assert "fixture-token" not in repr(instance.signer)
        for message in (
            b"arbitrary",
            b"CheckedFlow/command/v1\0{}",
            DOMAIN + b"x" * 1048576,
            "text",
        ):
            with pytest.raises(Failure, match="SIGNATURE"):
                instance.signer.sign(message)
        assert len(instance.requests) == 2


def test_metadata_and_signature_fail_closed(tmp_path):
    with service(tmp_path) as instance:
        original = dict(instance.metadata)
        for changes in (
            {"type": "rsa-2048"},
            {"derived": True},
            {"exportable": True},
            {"allow_plaintext_backup": True},
            {"supports_signing": False},
            {"keys": {}},
            {"keys": {"1": {"public_key": "%%%"}}},
        ):
            instance.metadata = original | changes
            before = len(instance.requests)
            with pytest.raises(Failure, match="SIGNER_BINDING"):
                instance.signer.sign(MESSAGE)
            assert len(instance.requests) == before + 1
        instance.metadata = original
        with pytest.raises(Failure, match="provider key differs"):
            replace(
                instance.signer, credential=replace(instance.credential, public_key="0" * 64)
            ).sign(MESSAGE)
        for signature in (
            [],
            {"private": "must-not-appear"},
            "vault:v2:" + base64.b64encode(instance.key.sign(MESSAGE)).decode(),
            "vault:v2:" + "A" * 88,
            "other:v1:abc",
            "vault:v1:%%%",
            "vault:v1:AA==",
            "vault:v1:" + base64.b64encode(b"x" * 64).decode(),
        ):
            instance.signature = signature
            with pytest.raises(Failure, match="SIGNER_BINDING"):
                instance.signer.sign(MESSAGE)


def test_transport_failures_redact_provider_data_and_do_not_redirect(tmp_path):
    with service(tmp_path) as instance:
        for status in (403, 401, 307, 500, 503):
            instance.status = status
            instance.raw = b"fixture-token private provider details"
            before = len(instance.requests)
            with pytest.raises(Failure) as error:
                instance.signer.sign(MESSAGE)
            assert "fixture-token" not in str(error.value)
            assert "private provider" not in str(error.value)
            assert len(instance.requests) == before + 1
        instance.status = 200
        for body in (b"{}", b"not-json", b'{"data": {}, "data": {}}', b"x" * (MAX_RESPONSE + 1)):
            instance.raw = body
            with pytest.raises(Failure, match="SIGNER_RESPONSE"):
                instance.signer.sign(MESSAGE)
        instance.raw = None
        for length in ("invalid", -1, 20000):
            instance.declared_length = length
            with pytest.raises(Failure, match="SIGNER_RESPONSE"):
                instance.signer.sign(MESSAGE)
        instance.declared_length, instance.encoding = None, "gzip"
        with pytest.raises(Failure, match="compressed"):
            instance.signer.sign(MESSAGE)
        instance.encoding, instance.delay = None, 1.5
        started = time.monotonic()
        with pytest.raises(Failure, match="SIGNER_UNAVAILABLE"):
            replace(instance.signer, timeout_seconds=1).sign(MESSAGE)
        assert time.monotonic() - started < 3
    with pytest.raises(Failure, match="SIGNER_UNAVAILABLE"):
        instance.signer.sign(MESSAGE)


def test_tls_requires_trusted_certificate_and_matching_host(tmp_path):
    with service(tmp_path, tls=True) as instance:
        with pytest.raises(Failure, match="SIGNER_UNAVAILABLE"):
            instance.signer.sign(MESSAGE)
        trusted = replace(instance.signer, ca_file=instance.ca)
        assert trusted.sign(MESSAGE) == instance.key.sign(MESSAGE)
        with pytest.raises(Failure, match="SIGNER_UNAVAILABLE"):
            replace(trusted, endpoint=trusted.endpoint.replace("127.0.0.1", "localhost")).sign(
                MESSAGE
            )


def test_configuration_rejects_unsafe_origins_paths_and_headers(tmp_path):
    with service(tmp_path) as instance:
        for change in (
            {"endpoint": "http://127.0.0.1", "allow_insecure_loopback": False},
            {"endpoint": "http://localhost"},
            {"endpoint": "http://192.0.2.1"},
            {"endpoint": "https://user:secret@host.invalid"},
            {"endpoint": "https://host.invalid/path"},
            {"endpoint": "https://host.invalid?token=secret"},
            {"endpoint": "https://host.invalid#x"},
            {"endpoint": "https://[invalid"},
            {"endpoint": "https://host.invalid:65536"},
            {"endpoint": "file:///tmp/socket"},
            {"endpoint": "https://host.invalid\n"},
            {"token": "bad\r\nheader"},
            {"token": ""},
            {"key_name": "../other"},
            {"mount": "transit/other"},
            {"key_version": 0},
            {"timeout_seconds": 0},
        ):
            with pytest.raises(Failure):
                replace(instance.signer, **change)
