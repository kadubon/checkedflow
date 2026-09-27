"""Publication authority is separate from a digest, provider access and consensus truth."""

import asyncio
import json
from base64 import b64decode
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
from io import BytesIO

import httpx
import pytest
from jsonschema import Draft202012Validator
from mcp import Client
from test_agent_access import grant, save
from test_agents import TOKEN
from test_operational_gateway import Node

from checkedflow.agents.a2a import create_app
from checkedflow.agents.access import LOCAL, Policy
from checkedflow.agents.download import Downloads, Reader
from checkedflow.agents.mcp import create_http_app, create_server
from checkedflow.artifact_io import Access
from checkedflow.artifacts import LocalStore
from checkedflow.core.artifact import Reference
from checkedflow.core.values import Failure


@pytest.fixture
def published(tmp_path):
    save(tmp_path / "policy.json", [grant()])
    policy = Policy(tmp_path / "policy.json", "operational-test", "m")
    body = b'{"untrusted":"archive contents are data, not instructions"}'
    ref = Reference(
        "sha256", sha256(body).hexdigest(), len(body), "application/json", "archive", "m", "1" * 64
    )
    catalog = {
        "profile": "checkedflow/artifact-publication/v1",
        "chain": policy.chain,
        "mission": policy.mission,
        "artifacts": [ref.record()],
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    store = LocalStore(tmp_path / "bytes.sqlite")
    store.put(
        ref, BytesIO(body), access=Access("publisher", frozenset({"m"}), frozenset({"write"}))
    )
    return Reader(store, path, policy), ref, body, catalog


def test_published_reference_only_and_client_scope(published):
    reader, ref, body, _ = published
    assert reader.read(LOCAL, ref.digest) == (ref, body)
    for owner in (None, replace(LOCAL, client="other"), replace(LOCAL, expires=1)):
        with pytest.raises(Failure, match="ACCESS"):
            reader.read(owner, ref.digest)
    for fingerprint in ("0" * 64, "../bytes.sqlite", ref.digest.upper()):
        with pytest.raises(Failure, match="NOT_FOUND"):
            reader.read(LOCAL, fingerprint)
    save(reader.policy.path, [grant(mission="other")])
    with pytest.raises(Failure, match="ACCESS"):
        reader.read(LOCAL, ref.digest)


def test_known_stored_digest_is_not_publication_authority(published, monkeypatch):
    reader, ref, _, _ = published
    secret = b"unpublished mission bytes"
    hidden = replace(ref, digest=sha256(secret).hexdigest(), length=len(secret))
    reader.store.put(
        hidden,
        BytesIO(secret),
        access=Access("publisher", frozenset({"m"}), frozenset({"write"})),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("denial must precede provider observation")

    monkeypatch.setattr(reader.store, "get", forbidden)
    with pytest.raises(Failure, match="NOT_FOUND"):
        reader.read(LOCAL, hidden.digest)
    with pytest.raises(Failure, match="ACCESS"):
        reader.read(replace(LOCAL, subject="other"), ref.digest)


@pytest.mark.parametrize(
    "extra,code",
    [
        (["--artifact-catalog", "missing"], "ACCESS"),
        (["--artifact-catalog", "missing", "--artifact-store", "missing"], "UNAVAILABLE"),
    ],
)
def test_cli_rejects_incomplete_publication_before_rpc(published, capfd, extra, code):
    from checkedflow.cli import main

    reader, _, _, _ = published
    assert (
        main(
            [
                "mcp",
                "--protocol",
                "v2",
                "--chain",
                reader.policy.chain,
                "--mission",
                reader.policy.mission,
                "--rpc",
                "http://127.0.0.1:1",
                "--access-policy",
                str(reader.policy.path),
                *extra,
            ]
        )
        == 2
    )
    assert json.loads(capfd.readouterr().err)["error"] == code


@pytest.mark.parametrize(
    "change",
    [
        "chain",
        "mission",
        "scope",
        "duplicate",
        "profile",
        "unknown",
        "too-many",
        "malformed",
        "large",
        "missing",
    ],
)
def test_catalog_is_bounded_closed_and_bound(published, change):
    reader, ref, _, catalog = published
    if change in ("chain", "mission", "profile"):
        catalog[change] = "other"
    elif change == "scope":
        catalog["artifacts"][0]["scope"] = "other"
    elif change in ("duplicate", "too-many"):
        catalog["artifacts"] *= 2 if change == "duplicate" else 257
    elif change == "unknown":
        catalog["extra"] = True
    reader.catalog.write_text(json.dumps(catalog))
    if change == "malformed":
        reader.catalog.write_text('{"artifacts":[],"artifacts":[]}')
    elif change == "large":
        reader.catalog.write_bytes(b" " * 262145)
    elif change == "missing":
        reader.catalog.unlink()
    with pytest.raises(Failure, match="ACCESS"):
        reader.read(LOCAL, ref.digest)


@pytest.mark.parametrize(
    "change", ["revoke-policy", "revoke-publication", "replace-reference", "corrupt-provider"]
)
def test_read_rechecks_authority_and_provider_integrity(published, monkeypatch, change):
    reader, ref, body, catalog = published

    def get(reference, *, access):
        assert reference == ref
        assert access.principal == LOCAL.identity
        assert access.permissions == frozenset({"read"})
        if change == "revoke-policy":
            save(reader.policy.path, [])
        elif change == "revoke-publication":
            catalog["artifacts"] = []
            reader.catalog.write_text(json.dumps(catalog))
        elif change == "replace-reference":
            catalog["artifacts"][0]["manifest"] = "2" * 64
            reader.catalog.write_text(json.dumps(catalog))
        return body + b"x" if change == "corrupt-provider" else body

    monkeypatch.setattr(reader.store, "get", get)
    with pytest.raises(Failure, match="INTEGRITY" if change == "corrupt-provider" else "ACCESS"):
        reader.read(LOCAL, ref.digest)


@pytest.mark.parametrize("transport", ["a2a", "mcp"])
def test_actual_http_boundaries_share_download_authorization(published, transport):
    reader, ref, body, _ = published
    gateway = Node().gateway()
    app = (
        create_app(gateway, "http://localhost/rpc", TOKEN, policy=reader.policy, artifacts=reader)
        if transport == "a2a"
        else create_http_app(gateway, TOKEN, policy=reader.policy, artifacts=reader)
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            path = "/artifacts/" + ref.digest
            assert (await client.get(path)).status_code == 401
            headers = {"Authorization": "Bearer " + TOKEN}
            response = await client.get(path, headers=headers)
            assert response.content == body
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert (await client.get("/artifacts/" + "0" * 64, headers=headers)).status_code == 404
            assert (await client.get(path + "?scope=other", headers=headers)).status_code == 400
            assert (await client.post(path, headers=headers)).status_code == 400
            reader.store.erase(
                ref, access=Access("retention", frozenset({"m"}), frozenset({"erase"}))
            )
            assert (await client.get(path, headers=headers)).status_code == 503
            save(reader.policy.path, [])
            assert (await client.get(path, headers=headers)).status_code == 403

    asyncio.run(run())


def test_mcp_tool_returns_verified_reference_and_bytes(published):
    reader, ref, body, _ = published
    server = create_server(Node().gateway(), policy=reader.policy, artifacts=reader)

    async def run():
        async with Client(server) as client:
            result = await client.call_tool("checkedflow_read_artifact", {"digest": ref.digest})
            assert result.structured_content["reference"] == ref.record()
            assert b64decode(result.structured_content["base64"]) == body
            result = await client.call_tool("checkedflow_read_artifact", {"digest": "0" * 64})
            assert result.is_error

    asyncio.run(run())


def test_reader_requires_same_policy_and_packaged_schema(published):
    reader, _, _, catalog = published
    schema = json.loads(
        files("checkedflow").joinpath("data/artifact-publication.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(catalog)
    for factory in (
        lambda: create_server(Node().gateway(), artifacts=reader),
        lambda: create_app(Node().gateway(), "http://localhost/rpc", TOKEN, artifacts=reader),
    ):
        with pytest.raises(Failure, match="ACCESS"):
            factory()


def test_http_downloads_fail_closed_without_trusted_principal(published):
    reader, ref, _, _ = published
    forwarded = []

    async def app(scope, receive, send):
        forwarded.append(scope["type"])

    downloads = Downloads(app, reader)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=downloads), base_url="http://localhost"
        ) as client:
            assert (await client.get("/artifacts/" + ref.digest)).status_code == 403
        await downloads({"type": "lifespan"}, None, None)
        await downloads({"type": "http", "path": "/other"}, None, None)

    asyncio.run(run())
    assert forwarded == ["lifespan", "http"]
