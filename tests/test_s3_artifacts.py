"""Hostile transport contracts; real provider behavior is a separate required service test."""

from dataclasses import replace
from hashlib import sha256
from io import BytesIO

import httpx
import pytest

from checkedflow import s3_artifacts
from checkedflow.artifacts import Access
from checkedflow.core.artifact import Reference
from checkedflow.core.values import Failure
from checkedflow.s3_artifacts import Credentials, S3Store

CREDS = Credentials("fixture-access", "fixture-secret", "fixture-session")
ACCESS = Access("fixture", frozenset({"mission"}), frozenset({"read", "write"}))
BODY = b"artifact"


def reference(body=BODY):
    return Reference(
        "sha256", sha256(body).hexdigest(), len(body), "text/plain", "evidence", "mission", "a" * 64
    )


def store(**kwargs):
    return S3Store("https://objects.example", "fixture-bucket", CREDS, **kwargs)


def transport(monkeypatch, handler):
    original = httpx.Client
    calls = []

    def handle(request):
        calls.append(request)
        response = handler(request)
        if response.is_stream_consumed:
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=Chunks([response.content]),
            )
        return response

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        assert kwargs["verify"].check_hostname
        assert 0 < kwargs["timeout"] <= 30
        return original(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(s3_artifacts.httpx, "Client", client)
    return calls


def test_explicit_credentials_and_configuration(tmp_path):
    assert "fixture" not in repr(CREDS)
    for values in (("", "x"), (None, "x"), ("x", None), ("x", "secret\n"), ("a" * 129, "x")):
        with pytest.raises(Failure, match="CREDENTIAL"):
            Credentials(*values)
    with pytest.raises(Failure, match="CREDENTIAL"):
        Credentials("x", "y", "")
    for endpoint in (
        "file:///tmp",
        "https://",
        "https://user:secret@objects.example",
        "https://a/path",
        "https://a?x=1",
        "https://a#fragment",
        "https://a:0",
        "https://a:65536",
        "https://a:bad",
        "https://a b",
        "https://%61",
        "https://é.example",
        "https://[bad",
    ):
        with pytest.raises(Failure, match="ENDPOINT"):
            S3Store(endpoint, "fixture-bucket", CREDS)
    for endpoint in ("http://objects.example", "http://localhost", "http://127.0.0.2"):
        with pytest.raises(Failure, match="TLS"):
            S3Store(endpoint, "fixture-bucket", CREDS, allow_loopback_http=True)
    with pytest.raises(Failure, match="TLS"):
        S3Store("http://127.0.0.1", "fixture-bucket", CREDS)
    with pytest.raises(Failure, match="TLS"):
        store(allow_loopback_http="false")
    assert (
        S3Store(
            "http://127.0.0.1:1234/", "fixture-bucket", CREDS, allow_loopback_http=True
        ).endpoint
        == "http://127.0.0.1:1234"
    )
    for bucket in ("ab", "-abc", "abc-", "a.b", "A-b", "a" * 64):
        with pytest.raises(Failure, match="SCOPE"):
            S3Store("https://objects.example", bucket, CREDS)
    for kwargs in ({"prefix": "../x"}, {"region": ""}, {"prefix": "x" * 65}):
        with pytest.raises(Failure, match="SCOPE"):
            store(**kwargs)
    for timeout in (True, 0, -1, 31, float("nan"), float("inf")):
        with pytest.raises(Failure, match="TIMEOUT"):
            store(timeout=timeout)
    with pytest.raises(Failure, match="TLS"):
        store(ca_file=tmp_path / "missing.pem")


def test_auth_and_input_integrity_precede_any_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unauthorized I/O")

    instance = store()
    monkeypatch.setattr(instance, "_request", forbidden)

    class Unreadable:
        read = forbidden

    for denied in (
        replace(ACCESS, scopes=frozenset({"other"})),
        replace(ACCESS, permissions=frozenset()),
    ):
        with pytest.raises(Failure, match="AUTHORITY"):
            instance.get(reference(), access=denied)
        with pytest.raises(Failure, match="AUTHORITY"):
            instance.put(reference(), Unreadable(), access=denied)
    with pytest.raises(Failure, match="INTEGRITY"):
        instance.put(reference(), BytesIO(b"modified"), access=ACCESS)


@pytest.mark.parametrize("put_status", [200, 409, 412])
def test_signed_conditional_publication_always_verifies_bytes(monkeypatch, put_status):
    def handler(request):
        assert request.url.path == "/fixture-bucket/checkedflow/mission/" + reference().digest
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
        assert "fixture-access/" in request.headers["authorization"]
        assert request.headers["x-amz-security-token"] == "fixture-session"
        if request.method == "PUT":
            assert request.headers["if-none-match"] == "*"
            assert "if-none-match" in request.headers["authorization"]
            assert request.headers["content-type"] == "text/plain"
            assert request.content == BODY
            return httpx.Response(put_status, content=b"")
        return httpx.Response(200, content=BODY, headers={"etag": '"deliberately-untrusted"'})

    calls = transport(monkeypatch, handler)
    store().put(reference(), BytesIO(BODY), access=ACCESS)
    assert [call.method for call in calls] == ["PUT", "GET"]


def test_lost_write_acknowledgement_is_read_back_without_resending(monkeypatch):
    def handler(request):
        if request.method == "PUT":
            raise httpx.ReadError("secret upstream diagnostics")
        return httpx.Response(200, content=BODY)

    calls = transport(monkeypatch, handler)
    store().put(reference(), BytesIO(BODY), access=ACCESS)
    assert [call.method for call in calls] == ["PUT", "GET"]


@pytest.mark.parametrize("status", [401, 403, 404, 301, 307, 500])
def test_error_and_redirect_responses_cannot_become_bytes(monkeypatch, status):
    calls = transport(
        monkeypatch, lambda _: httpx.Response(status, headers={"location": "https://evil.example"})
    )
    with pytest.raises(Failure, match="AUTHORITY" if status in {401, 403} else "UNAVAILABLE"):
        store().get(reference(), access=ACCESS)
    assert len(calls) == 1
    with pytest.raises(Failure, match="AUTHORITY" if status in {401, 403} else "OUTCOME_UNKNOWN"):
        store().put(reference(), BytesIO(BODY), access=ACCESS)
    assert len(calls) == 2


@pytest.mark.parametrize("body", [b"", b"art", b"modified", b"artifact!"])
def test_read_hash_and_length_are_authoritative(monkeypatch, body):
    transport(monkeypatch, lambda _: httpx.Response(200, content=body))
    with pytest.raises(Failure, match="INTEGRITY"):
        store().get(reference(), access=ACCESS)


@pytest.mark.parametrize(
    "headers,code",
    [
        ({"content-encoding": "gzip"}, "TRANSPORT"),
        ({"content-length": "9"}, "INTEGRITY"),
        ({"content-length": "-1"}, "INTEGRITY"),
        ({"content-length": "nan"}, "INTEGRITY"),
        ({"content-length": "9" * 5000}, "INTEGRITY"),
    ],
)
def test_hostile_response_headers_are_bounded(monkeypatch, headers, code):
    # Raw stream avoids the test client's automatic decompression before adapter checks.
    transport(monkeypatch, lambda _: httpx.Response(200, headers=headers, stream=Chunks([BODY])))
    with pytest.raises(Failure, match=code):
        store().get(reference(), access=ACCESS)


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


def test_chunked_body_limit_and_empty_object(monkeypatch):
    bodies = iter(([BODY, b"!"], []))
    transport(monkeypatch, lambda _: httpx.Response(200, stream=Chunks(next(bodies))))
    with pytest.raises(Failure, match="INTEGRITY"):
        store().get(reference(), access=ACCESS)
    assert store().get(reference(b""), access=ACCESS) == b""


@pytest.mark.parametrize("delay_at", ["headers", "body"])
def test_elapsed_response_limit(monkeypatch, delay_at):
    clock = iter([0, 31] if delay_at == "headers" else [0, 0, 31])
    monkeypatch.setattr(s3_artifacts.time, "monotonic", lambda: next(clock))
    # MockTransport itself does not use monotonic, but httpx's timing machinery does.
    instance = store()

    class Response:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_raw(self):
            yield BODY

    class Client(Response):
        def __init__(self, **kwargs):
            pass

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(s3_artifacts.httpx, "Client", Client)
    with pytest.raises(Failure, match="TRANSPORT"):
        instance.get(reference(), access=ACCESS)


@pytest.mark.parametrize("failure", [httpx.ReadError("fixture-secret"), OSError("fixture-secret")])
def test_outage_stays_unknown_and_diagnostics_hide_credentials(monkeypatch, failure):
    def handler(request):
        raise failure

    calls = transport(monkeypatch, handler)
    instance = store()
    with pytest.raises(Failure, match="TRANSPORT") as caught:
        instance.get(reference(), access=ACCESS)
    assert "fixture-secret" not in str(caught.value)
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        instance.put(reference(), BytesIO(BODY), access=ACCESS)
    assert [call.method for call in calls] == ["GET", "PUT", "GET"]


def test_conflicting_existing_object_cannot_be_repaired(monkeypatch):
    calls = transport(
        monkeypatch,
        lambda r: (
            httpx.Response(412) if r.method == "PUT" else httpx.Response(200, content=b"modified")
        ),
    )
    with pytest.raises(Failure, match="INTEGRITY"):
        store().put(reference(), BytesIO(BODY), access=ACCESS)
    assert [call.method for call in calls] == ["PUT", "GET"]
