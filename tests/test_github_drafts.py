"""Bounded provider protocol fixtures and actual SQLite dispatch claims."""

import copy
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, Thread

import httpx
import pytest

from checkedflow.core.values import Failure
from checkedflow.github_drafts import (
    API_VERSION,
    MAX_RESPONSE,
    Drafts,
    Outcome,
    Plan,
    Token,
    decode_plan,
)
from checkedflow.wire import dumps


def plan():
    return Plan(
        "owner/fixture",
        42,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "main",
        "1" * 40,
        "2" * 40,
        "3" * 40,
    )


def pull(selected=None, number=7):
    selected = selected or plan()
    return dict(
        number=number,
        draft=True,
        state="open",
        title=selected.title,
        body=selected.body,
        user={"login": "owner"},
        head={"ref": selected.branch, "sha": selected.head_commit, "repo": {"id": 42}},
        base={"ref": selected.base_branch, "sha": selected.base_commit, "repo": {"id": 42}},
    )


class Provider:
    def __init__(self):
        self.requests = []
        self.rows = []
        self.repo = {"id": 42, "full_name": "owner/fixture", "archived": False}
        self.enabled = False
        self.branch = plan().head_commit
        self.tree = plan().git_tree
        self.parent = plan().base_commit
        self.fail = ""
        self.barrier = None

    def handle(self, request):
        self.requests.append(request)
        assert request.url.host == "api.github.com"
        assert request.headers["x-github-api-version"] == API_VERSION
        assert request.headers["authorization"] == "Bearer fixture-secret"
        path = request.url.path.removeprefix("/repos/owner/fixture")
        if request.method == "POST":
            assert path == "/pulls"
            payload = json.loads(request.content)
            assert payload["draft"] is True and payload["maintainer_can_modify"] is False
            assert payload["head"] == plan().branch and payload["base"] == "main"
            if self.fail != "before":
                self.rows = [pull()]
            if self.fail:
                raise httpx.ReadTimeout("fixture lost reply", request=request)
            return httpx.Response(201, json=self.rows[0])
        assert request.method == "GET"
        if not path:
            data = self.repo
        elif path == "/actions/permissions":
            data = {"enabled": self.enabled}
        elif path.startswith("/git/ref/heads/"):
            data = {
                "object": {
                    "type": "commit",
                    "sha": self.parent if path.endswith("/main") else self.branch,
                }
            }
        elif path.startswith("/git/commits/"):
            data = {
                "sha": plan().head_commit,
                "tree": {"sha": self.tree},
                "parents": [{"sha": self.parent}],
            }
        else:
            assert path == "/pulls"
            assert request.url.params["state"] == "all"
            assert "base" not in request.url.params  # A human-changed base cannot hide an old PR.
            data = copy.deepcopy(self.rows)
            if self.barrier:
                self.barrier.wait(timeout=5)
        return httpx.Response(200, json=data)

    @property
    def posts(self):
        return sum(request.method == "POST" for request in self.requests)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    remote = Provider()
    original = httpx.Client

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return original(**kwargs, transport=httpx.MockTransport(remote.handle))

    monkeypatch.setattr("checkedflow.github_drafts.httpx.Client", client)
    adapter = Drafts(
        "owner/fixture",
        42,
        "owner",
        Token("fixture-secret"),
        tmp_path / "journal.sqlite",
        enabled=True,
    )
    return adapter, remote


def test_disabled_before_any_io(fixture):
    adapter, remote = fixture
    adapter.enabled = False
    for call in (adapter.dispatch, adapter.reconcile):
        with pytest.raises(Failure, match="DISABLED"):
            call(plan())
    assert not remote.requests and not adapter.journal.exists()
    assert "fixture-secret" not in repr(adapter.token)


def test_confirmed_dispatch_is_durable_and_idempotent(fixture):
    adapter, remote = fixture
    assert adapter.dispatch(plan()) == Outcome("confirmed", 7)
    assert remote.posts == 1
    reopened = Drafts(adapter.repository, 42, "owner", adapter.token, adapter.journal, enabled=True)
    assert reopened.dispatch(plan()) == Outcome("confirmed", 7)
    assert remote.posts == 1
    assert reopened.reconcile(plan()) == Outcome("confirmed", 7)
    with pytest.raises(Failure, match="CONFLICT"):
        reopened.dispatch(replace(plan(), patch="e" * 64))


@pytest.mark.parametrize("failure", ["STOPPED", "NOT_READY", "AUTHORITY", "POLICY"])
def test_final_guard_rejects_without_post_or_retry(fixture, failure):
    adapter, remote = fixture
    calls = []

    def reject(selected):
        calls.append(selected)
        assert remote.requests[-1].url.path.endswith("/pulls")
        assert remote.posts == 0
        # Read a second connection: the unknown claim must already be durable.
        with adapter._db() as db:
            assert adapter._prior(db, selected) == Outcome("unknown")
        raise Failure(failure, "fixture authority lost during provider reads")

    assert adapter.dispatch(plan(), before_send=reject) == Outcome("unknown")
    assert calls == [plan()]
    reopened = Drafts(adapter.repository, 42, "owner", adapter.token, adapter.journal, enabled=True)
    assert reopened.dispatch(plan()) == Outcome("unknown")
    assert reopened.reconcile(plan()) == Outcome("unknown")
    assert remote.posts == 0


def test_final_guard_success_precedes_only_post(fixture):
    adapter, remote = fixture
    calls = []

    def approve(selected):
        calls.append(selected)
        assert remote.requests[-1].url.path.endswith("/pulls")
        assert remote.posts == 0

    assert adapter.dispatch(plan(), before_send=approve) == Outcome("confirmed", 7)
    assert calls == [plan()] and remote.posts == 1
    assert adapter.dispatch(plan(), before_send=approve) == Outcome("confirmed", 7)
    assert calls == [plan()] and remote.posts == 1


def test_final_guard_existing_observation_does_not_grant_new_send(fixture):
    adapter, remote = fixture
    remote.rows = [pull()]

    def reject(selected):
        pytest.fail("read observation must not request new-send authority")

    assert adapter.dispatch(plan(), before_send=reject) == Outcome("confirmed", 7)
    assert remote.posts == 0


def test_final_guard_local_disable_is_rechecked(fixture):
    adapter, remote = fixture

    def disable(selected):
        adapter.enabled = False

    assert adapter.dispatch(plan(), before_send=disable) == Outcome("unknown")
    adapter.enabled = True
    assert adapter.dispatch(plan()) == Outcome("unknown")
    assert remote.posts == 0


def test_final_guard_unexpected_exception_preserves_claim(fixture):
    adapter, remote = fixture

    def crash(selected):
        raise RuntimeError("fixture supervisor failure")

    with pytest.raises(RuntimeError):
        adapter.dispatch(plan(), before_send=crash)
    assert adapter.dispatch(plan()) == Outcome("unknown")
    assert remote.posts == 0


@pytest.mark.parametrize("when", ["before", "after"])
def test_lost_reply_never_causes_post_retry(fixture, when):
    adapter, remote = fixture
    remote.fail = when
    assert adapter.dispatch(plan()) == Outcome("unknown")
    remote.fail = ""
    assert adapter.dispatch(plan()) == Outcome("unknown")
    assert adapter.reconcile(plan()) == (
        Outcome("unknown") if when == "before" else Outcome("confirmed", 7)
    )
    assert remote.posts == 1
    if when == "before":
        remote.rows = [pull()]  # Delayed observation of the original operation.
        assert adapter.reconcile(plan()) == Outcome("confirmed", 7)
        assert remote.posts == 1


def test_existing_exact_owned_draft_is_observed_without_creation(fixture):
    adapter, remote = fixture
    remote.rows = [pull()]
    assert adapter.dispatch(plan()) == Outcome("confirmed", 7)
    assert remote.posts == 0


def test_concurrent_dispatch_claim_is_serialized(fixture):
    adapter, remote = fixture
    remote.barrier = Barrier(2)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: adapter.dispatch(plan()), range(2)))
    assert all(result.status in {"unknown", "confirmed"} for result in results)
    assert Outcome("confirmed", 7) in results
    assert remote.posts == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", True),
        ("branch", "9" * 40),
        ("tree", "9" * 40),
        ("parent", "9" * 40),
        ("repo", {"id": 99, "full_name": "owner/fixture", "archived": False}),
    ],
)
def test_preflight_refuses_changed_policy_or_content(fixture, field, value):
    adapter, remote = fixture
    setattr(remote, field, value)
    with pytest.raises(Failure):
        adapter.dispatch(plan())
    assert remote.posts == 0


@pytest.mark.parametrize(
    "change", ["human-title", "human-base", "ready", "closed", "foreign", "head", "duplicate"]
)
def test_existing_changed_or_foreign_object_cannot_be_adopted(fixture, change):
    adapter, remote = fixture
    row = pull()
    if change == "human-title":
        row["title"] = "human edit"
    elif change == "human-base":
        row["base"]["ref"] = "other"
    elif change == "ready":
        row["draft"] = False
    elif change == "closed":
        row["state"] = "closed"
    elif change == "foreign":
        row["user"]["login"] = "other"
    elif change == "head":
        row["head"]["repo"]["id"] = 99
    remote.rows = [row, row] if change == "duplicate" else [row]
    with pytest.raises(Failure, match="CONFLICT"):
        adapter.dispatch(plan())
    assert remote.posts == 0


def test_changed_observation_does_not_overwrite_historical_receipt(fixture):
    adapter, remote = fixture
    assert adapter.dispatch(plan()) == Outcome("confirmed", 7)
    remote.rows = [pull(number=8)]
    assert adapter.reconcile(plan()) == Outcome("unknown")
    assert adapter.dispatch(plan()) == Outcome("confirmed", 7)
    remote.rows[0]["state"] = "closed"
    assert adapter.reconcile(plan()) == Outcome("unknown")
    assert remote.posts == 1


def test_confirmation_storage_failure_keeps_committed_unknown_claim(fixture, monkeypatch):
    adapter, remote = fixture
    original = adapter._confirmed

    def fail(*args):
        raise sqlite3.OperationalError("fixture disk interruption")

    monkeypatch.setattr(adapter, "_confirmed", fail)
    assert adapter.dispatch(plan()) == Outcome("unknown")
    assert adapter.dispatch(plan()) == Outcome("unknown")
    monkeypatch.setattr(adapter, "_confirmed", original)
    assert adapter.reconcile(plan()) == Outcome("confirmed", 7)
    assert remote.posts == 1


def test_journal_identity_and_corruption_fail_closed(fixture):
    adapter, _ = fixture
    with pytest.raises(Failure, match="NOT_FOUND"):
        adapter.reconcile(plan())
    assert adapter.dispatch(plan()) == Outcome("confirmed", 7)
    other = Drafts("owner/other", 42, "owner", adapter.token, adapter.journal, enabled=True)
    with pytest.raises(Failure, match="SCOPE"):
        other.dispatch(plan())
    with pytest.raises(Failure, match="SCOPE"):
        other.dispatch(replace(plan(), repository="owner/other"))
    with pytest.raises(Failure, match="STORAGE"):
        adapter._confirmed(replace(plan(), operation="f" * 64), 7)
    with closing(sqlite3.connect(adapter.journal)) as db, db:
        db.execute("DELETE FROM identity")
    with pytest.raises(Failure, match="STORAGE"):
        adapter.dispatch(plan())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "https://example.invalid"}),
        httpx.Response(403, json={"message": "denied"}),
        httpx.Response(200, content=b'{"id":42,"id":42}'),
        httpx.Response(200, content=b"x" * (MAX_RESPONSE + 1)),
        httpx.Response(200, json={}, headers={"link": '<https://example.invalid>; rel="next"'}),
    ],
)
def test_bounded_untrusted_provider_responses(fixture, monkeypatch, response):
    adapter, remote = fixture
    monkeypatch.setattr(remote, "handle", lambda request: response)
    with pytest.raises(Failure):
        adapter.dispatch(plan())
    assert remote.posts == 0


@pytest.mark.parametrize(
    "change",
    [
        {"operation": "bad"},
        {"base_branch": "../main"},
        {"head_commit": "1" * 40},
        {"git_tree": "g" * 40},
    ],
)
def test_plan_constraints(change):
    with pytest.raises(Failure):
        replace(plan(), **change)


def test_plan_destination_rejected_before_journal_or_network(fixture):
    adapter, remote = fixture
    for selected in (replace(plan(), repository="owner/other"), replace(plan(), repository_id=99)):
        with pytest.raises(Failure, match="SCOPE"):
            adapter.dispatch(selected)
    assert not remote.requests and not adapter.journal.exists()


@pytest.mark.parametrize("credential", ["", "token\nheader", "a" * 4097])
def test_token_constraints(credential):
    with pytest.raises(Failure, match="CREDENTIAL"):
        Token(credential)


def test_process_crash_at_send_boundary_cannot_retry(fixture):
    adapter, remote = fixture
    program = """
import os, sys
from pathlib import Path
from checkedflow.github_drafts import Drafts, Plan, Token
adapter = Drafts('owner/fixture', 42, 'owner', Token('fixture-secret'),
    Path(sys.argv[1]), enabled=True)
adapter._preflight = lambda plan: None
adapter._existing = lambda plan: 0
def crash(*args, **kwargs):
    os._exit(97)
adapter._request = crash
adapter.dispatch(Plan('owner/fixture',42,'a'*64,'b'*64,'c'*64,'d'*64,
    'main','1'*40,'2'*40,'3'*40))
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(adapter.journal)],
        capture_output=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 97, result.stderr.decode()
    assert adapter.dispatch(plan()) == Outcome("unknown")
    assert not remote.requests
    assert adapter.reconcile(plan()) == Outcome("unknown")
    assert remote.posts == 0


def test_plan_parser_is_metadata_only():
    selected = plan()
    assert decode_plan(dumps(selected.record())) == selected
    for raw in (
        b" " * 8193,
        dumps(selected.record() | {"extra": 1}),
        dumps(selected.record() | {"version": "future"}),
    ):
        with pytest.raises(Failure):
            decode_plan(raw)


def test_boolean_provider_identity_is_not_an_integer(fixture):
    adapter, remote = fixture
    adapter.repository_id = 1
    remote.repo["id"] = True
    with pytest.raises(Failure, match="SHAPE"):
        adapter.dispatch(replace(plan(), repository_id=1))
    assert remote.posts == 0


def test_real_loopback_protocol_server_preserves_lost_reply(tmp_path, monkeypatch):
    remote = Provider()
    remote.fail = "after"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_call(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = httpx.Request(
                self.command,
                "https://api.github.com" + self.path,
                headers=dict(self.headers),
                content=body,
            )
            try:
                response = remote.handle(request)
            except httpx.ReadTimeout:
                self.close_connection = True
                return
            payload = response.read()
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = handle_call
        do_POST = handle_call

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    original = httpx.Client

    class LocalTransport(httpx.BaseTransport):
        def handle_request(self, request):
            # Only the fixed fixture token reaches this deliberately local protocol server.
            assert request.headers["authorization"] == "Bearer fixture-secret"
            url = request.url.copy_with(scheme="http", host="127.0.0.1", port=server.server_port)
            with original(trust_env=False, timeout=2) as client:
                response = client.request(
                    request.method, url, headers=request.headers, content=request.read()
                )
                response.read()
                return response

    monkeypatch.setattr(
        "checkedflow.github_drafts.httpx.Client",
        lambda **kwargs: original(**kwargs, transport=LocalTransport()),
    )
    try:
        adapter = Drafts(
            "owner/fixture",
            42,
            "owner",
            Token("fixture-secret"),
            tmp_path / "journal.sqlite",
            enabled=True,
        )
        assert adapter.dispatch(plan()) == Outcome("unknown")
        assert adapter.dispatch(plan()) == Outcome("unknown")
        remote.fail = ""
        assert adapter.reconcile(plan()) == Outcome("confirmed", 7)
        assert remote.posts == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive()
