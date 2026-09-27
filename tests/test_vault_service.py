"""Real disposable Vault Transit test; no service is substituted when the binary is absent."""

import base64
import hashlib
import http.client
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import time
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

import checkedflow
from checkedflow.core.authority import Credential
from checkedflow.core.operational import genesis
from checkedflow.core.values import Failure
from checkedflow.operational_identity import DOMAIN, command_message, prove_possession, sign_command
from checkedflow.operational_runtime import Runtime
from checkedflow.vault_signer import VaultSigner


@pytest.mark.signer
def test_real_vault_version_binding_nonexport_policy_rotation_and_outage(tmp_path):
    if os.environ.get("CHECKEDFLOW_REQUIRE_INSTALLED_SIGNER") == "1":
        origin = Path(checkedflow.__file__).resolve().relative_to(Path(sys.prefix).resolve())
        assert "site-packages" in origin.parts
    configured = os.environ.get("CHECKEDFLOW_VAULT_BINARY")
    if not configured:
        if os.environ.get("CHECKEDFLOW_REQUIRE_SIGNER") == "1":
            pytest.fail("required pinned Vault executable missing")
        pytest.skip("real managed signer qualification was not requested")
    binary = Path(configured).resolve()
    provenance = json.loads((binary.parent / "provenance.json").read_text())
    platform_name = "windows" if os.name == "nt" else "linux"
    expected_archive = {
        "windows": "e07a39059d7c7380d6dc776fb5bee2183cbc3344cf9387d4cf11309b83c0dce3",
        "linux": "8aa90f9cea46f541fc7baa3d0ec692fc06afde9a248cc1f2dcac46a567c6f56b",
    }[platform_name]
    archive = binary.parent / f"vault_2.1.1_{platform_name}_amd64.zip"
    with archive.open("rb") as source:
        assert hashlib.file_digest(source, "sha256").hexdigest() == expected_archive
    with binary.open("rb") as source:
        binary_hash = hashlib.file_digest(source, "sha256").hexdigest()
    with (
        zipfile.ZipFile(archive) as bundle,
        bundle.open("vault.exe" if os.name == "nt" else "vault") as source,
    ):
        assert hashlib.file_digest(source, "sha256").hexdigest() == binary_hash
    assert binary_hash == provenance["binary_sha256"]
    assert provenance["archive_sha256"] == expected_archive
    assert provenance["version"] == "2.1.1"
    version = subprocess.run([str(binary), "version"], capture_output=True, timeout=10, check=True)
    assert version.stdout.startswith(b"Vault v2.1.1 ")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    root = secrets.token_urlsafe(32)
    certificates = tmp_path / "tls"
    certificates.mkdir(mode=0o700)
    ca_file = certificates / "vault-ca.pem"
    environment = {k: v for k, v in os.environ.items() if not k.startswith("VAULT_")}
    environment.update(VAULT_DEV_ROOT_TOKEN_ID=root, GOMAXPROCS="2", GOMEMLIMIT="256MiB")
    process = subprocess.Popen(
        [
            str(binary),
            "server",
            "-dev",
            "-dev-tls",
            f"-dev-tls-cert-dir={certificates}",
            "-dev-no-store-token",
            f"-dev-listen-address=127.0.0.1:{port}",
            "-log-level=error",
        ],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    def request(method, path, payload=None, *, token=root):
        connection = http.client.HTTPSConnection(
            "127.0.0.1", port, timeout=2, context=ssl.create_default_context(cafile=ca_file)
        )
        try:
            connection.request(
                method,
                "/v1/" + path,
                body=None if payload is None else json.dumps(payload),
                headers={"X-Vault-Token": token, "Content-Type": "application/json"},
            )
            reply = connection.getresponse()
            raw = reply.read(262145)
            assert len(raw) <= 262144
            return reply.status, json.loads(raw) if raw else {}
        finally:
            connection.close()

    try:
        ready_by = time.monotonic() + 20
        while True:
            assert process.poll() is None, "disposable Vault exited before readiness"
            try:
                status, health = request("GET", "sys/health")
                if status == 200 and health["version"] == "2.1.1":
                    break
            except OSError:
                pass
            assert time.monotonic() < ready_by, "Vault readiness exceeded 20 seconds"
            time.sleep(0.1)
        assert request("POST", "sys/mounts/transit", {"type": "transit"})[0] in {200, 204}
        assert request(
            "POST",
            "transit/keys/application",
            {"type": "ed25519", "exportable": False, "allow_plaintext_backup": False},
        )[0] in {200, 204}
        policy = (
            'path "transit/keys/application" { capabilities = ["read"] }\n'
            'path "transit/sign/application" { capabilities = ["update"] }'
        )
        assert request("PUT", "sys/policies/acl/sign-only", {"policy": policy})[0] in {200, 204}
        status, issued = request(
            "POST",
            "auth/token/create",
            {"policies": ["sign-only"], "no_default_policy": True, "ttl": "5m"},
        )
        assert status == 200
        limited = issued["auth"]["client_token"]

        def credential(revision, version):
            status, metadata = request("GET", "transit/keys/application", token=limited)
            assert status == 200
            public = base64.b64decode(
                metadata["data"]["keys"][str(version)]["public_key"], validate=True
            )
            return Credential("operator", revision, "a", "administrator", "", public.hex(), 0)

        signer = VaultSigner(
            credential(1, 1),
            f"https://127.0.0.1:{port}",
            "application",
            1,
            limited,
            ca_file=ca_file,
        )
        command = {
            "api_version": "checkedflow/v2",
            "chain": "vault-test",
            "epoch": 0,
            "id": "0:pause",
            "actor": "operator",
            "revision": 1,
            "nonce": 1,
            "kind": "mission.pause",
            "payload": {"mission": "m"},
        }
        raw = sign_command(command, {("operator", 1): signer})
        signature = bytes.fromhex(json.loads(raw)["signatures"][0]["signature"])
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(signer.credential.public_key)).verify(
            signature, command_message(command)
        )
        # A service-key version increase never silently selects the new key for an old binding.
        assert request("POST", "transit/keys/application/rotate", {}, token=limited)[0] == 403
        assert request("GET", "transit/export/signing-key/application", token=limited)[0] == 403
        assert request("GET", "transit/export/signing-key/application")[0] == 400
        assert request("POST", "transit/keys/application/rotate", {})[0] in {200, 204}
        assert signer.sign(command_message(command)) == signature
        second = replace(signer, credential=credential(2, 2), key_version=2)
        local_keys = {name: Ed25519PrivateKey.generate() for name in "bcd"}
        initial = genesis(
            "vault-test",
            "m",
            tuple("abcd"),
            (
                signer.credential,
                *(
                    Credential(
                        name,
                        1,
                        name,
                        "administrator",
                        "",
                        key.public_key().public_bytes_raw().hex(),
                        0,
                    )
                    for name, key in local_keys.items()
                ),
            ),
        )
        runtime = Runtime(initial)
        current_signers = {
            ("operator", 1): signer,
            **{(name, 1): key for name, key in local_keys.items()},
        }
        runtime.apply(sign_command(command, current_signers), height=1)
        proposal = command | {
            "id": "0:rotate",
            "nonce": 2,
            "kind": "key.schedule",
            "payload": {
                "mission": "m",
                "identity": "operator",
                "revision": 2,
                "public_key": second.credential.public_key,
                "activation_height": 3,
                "proof": "",
            },
        }
        runtime.apply(sign_command(prove_possession(proposal, second), current_signers), height=2)
        del current_signers[("operator", 1)]
        current_signers[("operator", 2)] = second
        runtime.apply(
            sign_command(command | {"id": "0:after", "nonce": 3, "revision": 2}, current_signers),
            height=3,
        )
        assert dict(runtime.state.journal.actors)["operator"] == 3
        newer = second.sign(DOMAIN + b"version two")
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(second.credential.public_key)).verify(
            newer, DOMAIN + b"version two"
        )
        with pytest.raises(Failure, match="SIGNER_BINDING"):
            replace(signer, key_version=2).sign(command_message(command))
        with pytest.raises(Failure, match="SIGNER_AUTH"):
            replace(signer, token="deliberately-invalid-fixture").sign(command_message(command))
        assert request("POST", "transit/keys/application/config", {"min_encryption_version": 2})[
            0
        ] in {200, 204}
        with pytest.raises(Failure, match="SIGNER_UNAVAILABLE"):
            signer.sign(command_message(command))
        assert second.sign(DOMAIN + b"version two") == newer
        assert request("POST", "auth/token/revoke", {"token": limited})[0] in {200, 204}
        with pytest.raises(Failure, match="SIGNER_AUTH"):
            second.sign(DOMAIN + b"denied after token revocation")
        process.terminate()
        process.wait(timeout=10)
        with pytest.raises(Failure, match="SIGNER_UNAVAILABLE"):
            second.sign(DOMAIN + b"unavailable")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
