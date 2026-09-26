"""Real pinned S3 service on private loopback listeners; never a replacement fake."""

import hashlib
import ipaddress
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import tarfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials as AwsCredentials
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import checkedflow
from checkedflow.artifacts import Access
from checkedflow.core.artifact import Reference
from checkedflow.core.values import Failure
from checkedflow.s3_artifacts import Credentials, S3Store


def tls(directory):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CheckedFlow disposable S3")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    ca, private = directory / "ca.pem", directory / "tls-key.pem"
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    return ca, private


@pytest.mark.object_store
def test_real_s3_tls_conditional_publication_corruption_and_outage(tmp_path):
    if os.environ.get("CHECKEDFLOW_REQUIRE_INSTALLED_S3") == "1":
        origin = Path(checkedflow.__file__).resolve().relative_to(Path(sys.prefix).resolve())
        assert "site-packages" in origin.parts
    configured = os.environ.get("CHECKEDFLOW_S3_BINARY")
    if not configured:
        if os.environ.get("CHECKEDFLOW_REQUIRE_S3") == "1":
            pytest.fail("required pinned S3 test executable missing")
        pytest.skip("real S3 service qualification was not requested")
    binary = Path(configured).resolve()
    windows = os.name == "nt"
    name = "windows_amd64.zip" if windows else "linux_amd64.tar.gz"
    expected = (
        "8809359079e62fcd60574ff661449160899622c52072f3f569d346669079efe9"
        if windows
        else "31fb804858885f9e7f18b6d3b1da09e824baac3e6a55b5a62c3c4c77e6ed6d7d"
    )
    archive = binary.parent / name
    with archive.open("rb") as source:
        assert hashlib.file_digest(source, "sha256").hexdigest() == expected
    with binary.open("rb") as source:
        fingerprint = hashlib.file_digest(source, "sha256").hexdigest()
    if windows:
        with zipfile.ZipFile(archive) as bundle, bundle.open("weed.exe") as source:
            assert hashlib.file_digest(source, "sha256").hexdigest() == fingerprint
    else:
        with tarfile.open(archive) as bundle, bundle.extractfile("weed") as source:
            assert hashlib.file_digest(source, "sha256").hexdigest() == fingerprint
    version = subprocess.run([str(binary), "version"], capture_output=True, check=True, timeout=10)
    assert b"4.47 c5073360007d28385a33426a42ac3e4ec504c5a3" in version.stdout
    listeners = []
    try:
        for _ in range(8):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listeners.append(listener)
        ports = [listener.getsockname()[1] for listener in listeners]
    finally:
        for listener in listeners:
            listener.close()
    ca, private = tls(tmp_path)
    admin = Credentials(secrets.token_hex(12), secrets.token_hex(24))
    writer = Credentials(secrets.token_hex(12), secrets.token_hex(24))
    bucket = "checkedflow-fixture"
    config = tmp_path / "s3.json"
    config.write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "name": "bootstrap",
                        "credentials": [
                            {"accessKey": admin.access_key, "secretKey": admin.secret_key}
                        ],
                        "actions": ["Admin", "Read", "Write", "List"],
                    },
                    {
                        "name": "artifact-writer",
                        "credentials": [
                            {"accessKey": writer.access_key, "secretKey": writer.secret_key}
                        ],
                        "actions": [f"Read:{bucket}", f"Write:{bucket}"],
                    },
                ]
            }
        )
    )
    config.chmod(0o600)
    (tmp_path / "filer.toml").write_text('[leveldb2]\nenabled = true\ndir = "./filer-data"\n')
    (tmp_path / "security.toml").write_text("# Disposable loopback-only test service.\n")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AWS_", "WEED_", "S3_", "SEAWEED_"))
    }
    environment.update(GOMAXPROCS="2", GOMEMLIMIT="256MiB")
    arguments = [
        str(binary),
        "server",
        "-ip=127.0.0.1",
        "-ip.bind=127.0.0.1",
        f"-dir={tmp_path}",
        "-master.telemetry=false",
        "-master.volumeSizeLimitMB=5",
        "-volume.max=2",
        f"-master.port={ports[0]}",
        f"-master.port.grpc={ports[1]}",
        f"-volume.port={ports[2]}",
        f"-volume.port.grpc={ports[3]}",
        "-filer",
        f"-filer.port={ports[4]}",
        f"-filer.port.grpc={ports[5]}",
        "-filer.exposeDirectoryData=false",
        "-filer.concurrentFileUploadLimit=4",
        "-filer.concurrentUploadLimitMB=16",
        "-s3",
        f"-s3.port={ports[6]}",
        f"-s3.port.grpc={ports[7]}",
        "-s3.ip.bind=127.0.0.1",
        "-s3.port.iceberg=0",
        "-s3.port.lance=0",
        "-s3.iam=false",
        f"-s3.config={config}",
        f"-s3.cert.file={ca}",
        f"-s3.key.file={private}",
    ]
    endpoint = f"https://127.0.0.1:{ports[6]}"
    context = ssl.create_default_context(cafile=ca)

    def request(method, path, body=b"", identity=admin):
        command = AWSRequest(method=method, url=endpoint + path, data=body)
        S3SigV4Auth(
            AwsCredentials(identity.access_key, identity.secret_key), "s3", "us-east-1"
        ).add_auth(command)
        with httpx.Client(
            verify=context, trust_env=False, follow_redirects=False, timeout=2
        ) as client:
            return client.request(
                method, endpoint + path, content=body, headers=dict(command.headers.items())
            ).status_code

    with (tmp_path / "service.log").open("wb") as log:
        process = subprocess.Popen(
            arguments,
            cwd=tmp_path,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if windows else 0,
        )
        try:
            deadline = time.monotonic() + 45
            while True:
                assert process.poll() is None, "S3 service exited before readiness"
                try:
                    if request("GET", "/") == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < deadline, "S3 readiness exceeded 45 seconds"
                time.sleep(0.2)
            assert request("PUT", "/" + bucket) == 200
            assert request("PUT", "/forbidden-bucket") == 200
            store = S3Store(endpoint, bucket, writer, ca_file=ca, timeout=3)
            body = b"verified service bytes"
            ref = Reference(
                "sha256",
                hashlib.sha256(body).hexdigest(),
                len(body),
                "text/plain",
                "evidence",
                "mission",
                "a" * 64,
            )
            access = Access("worker", frozenset({"mission"}), frozenset({"read", "write"}))
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: store.put(ref, BytesIO(body), access=access), range(4)))
            assert store.get(ref, access=access) == body
            # Empty and chunk-sized objects must use the same verified path, not ETag shortcuts.
            retained = []
            for payload in (b"", b"bounded persistence fixture\n" * 8192):
                item = replace(ref, digest=hashlib.sha256(payload).hexdigest(), length=len(payload))
                store.put(item, BytesIO(payload), access=access)
                assert store.get(item, access=access) == payload
                retained.append((item, payload))
            with httpx.Client(verify=context, trust_env=False, timeout=2) as anonymous:
                assert anonymous.get(store._url(ref)).status_code == 403
            with pytest.raises(Failure, match="UNAVAILABLE"):
                store.get(
                    replace(ref, scope="other"),
                    access=Access("other-reader", frozenset({"other"}), frozenset({"read"})),
                )
            for denied in (
                S3Store(endpoint, "forbidden-bucket", writer, ca_file=ca),
                S3Store(
                    endpoint, bucket, Credentials(writer.access_key, "wrong-secret"), ca_file=ca
                ),
            ):
                with pytest.raises(Failure, match="AUTHORITY"):
                    denied.get(ref, access=access)
                with pytest.raises(Failure, match="AUTHORITY"):
                    denied.put(ref, BytesIO(body), access=access)
            with pytest.raises(Failure, match="TRANSPORT"):
                S3Store(endpoint, bucket, writer, timeout=1).get(ref, access=access)
            path = f"/{bucket}/checkedflow/mission/{ref.digest}"
            assert request("PUT", path, b"corrupt server object") == 200
            with pytest.raises(Failure, match="INTEGRITY"):
                store.get(ref, access=access)
            with pytest.raises(Failure, match="INTEGRITY"):
                store.put(ref, BytesIO(body), access=access)
            assert request("DELETE", path) == 204
            with pytest.raises(Failure, match="UNAVAILABLE"):
                store.get(ref, access=access)
            process.terminate()
            process.wait(timeout=10)
            with pytest.raises(Failure, match="TRANSPORT"):
                store.get(ref, access=access)
            with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
                store.put(ref, BytesIO(body), access=access)
            # Restart the owned service over the same private data, without recreating the bucket.
            process = subprocess.Popen(
                arguments,
                cwd=tmp_path,
                env=environment,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if windows else 0,
            )
            deadline = time.monotonic() + 45
            while True:
                assert process.poll() is None, "S3 service exited during restart"
                try:
                    if request("GET", "/") == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < deadline, "S3 restart exceeded 45 seconds"
                time.sleep(0.2)
            for item, payload in retained:
                assert store.get(item, access=access) == payload
            with pytest.raises(Failure, match="UNAVAILABLE"):
                store.get(ref, access=access)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
