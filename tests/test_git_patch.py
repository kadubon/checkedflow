"""Independent Git plumbing oracle and byte-bound draft dispatch; no candidate execution."""

import json
import subprocess
from dataclasses import replace
from importlib.resources import files as resources

import httpx
import pytest
from test_github_drafts import Provider, plan, pull
from test_repository_patch import target

from checkedflow.core.values import Failure
from checkedflow.domains.repository_patch import Tree, apply_patch
from checkedflow.git_tree import tree_id
from checkedflow.github_drafts import Drafts, Outcome, Token


def test_packaged_portability_vectors():
    vectors = json.loads(
        resources("checkedflow").joinpath("data/git-tree-vectors.json").read_text(encoding="utf-8")
    )
    assert vectors["version"] == "checkedflow/git-tree-vectors/v1"
    assert vectors["object_format"] == "sha1"
    assert vectors["file_mode"] == "100644" and vectors["directory_mode"] == "40000"
    for case in vectors["cases"]:
        tree = Tree(tuple((row["path"], row["utf8"].encode()) for row in case["files"]))
        assert tree_id(tree) == case["git_tree"]


@pytest.mark.parametrize(
    "files",
    [
        (("a", b""),),
        (("a.c", b"hello\r\n"), ("a/b", b"unicode: \xc3\xa9\n"), ("a0", b"x")),
        (("a/b/c", b"1"), ("a/b0", b"2"), ("d/e", b"3"), ("z", b"4")),
    ],
)
def test_tree_matches_real_git(tmp_path, files):
    def git(*args, data=None):
        return (
            subprocess.run(
                ["git", "--git-dir=" + str(tmp_path / "objects.git"), *args],
                input=data,
                capture_output=True,
                check=True,
                timeout=10,
            )
            .stdout.strip()
            .decode("ascii")
        )

    git("init", "--bare", "--object-format=sha1", "--template=", str(tmp_path / "objects.git"))

    # mktree sorts the entries itself; the oracle does not implement Git's tree encoding.
    def build(entries):
        rows = []
        children = {}
        for name, content in entries:
            part, separator, rest = name.partition("/")
            if separator:
                children.setdefault(part, []).append((rest, content))
            else:
                oid = git("hash-object", "-w", "--stdin", data=content)
                rows.append(f"100644 blob {oid}\t{part}\n")
        for name, entries in children.items():
            rows.append(f"040000 tree {build(entries)}\t{name}\n")
        return git("mktree", data="".join(reversed(rows)).encode("ascii"))

    assert tree_id(Tree(files)) == build(files)


@pytest.fixture
def bound(tmp_path, monkeypatch):
    base, raw, contract = target()
    contract = replace(
        contract, repository=plan().repository, base_commit=plan().base_commit, allow_draft_pr=True
    )
    selected = replace(
        plan(),
        patch=contract.patch_digest,
        result=contract.result_tree,
        git_tree=tree_id(apply_patch(base, raw, contract)),
    )
    remote = Provider()
    remote.tree = selected.git_tree
    remote.base_tree = tree_id(base)
    remote.base_sha = selected.base_commit
    original = httpx.Client

    def handle(request):
        if request.url.path.endswith("/git/commits/" + selected.base_commit):
            remote.requests.append(request)
            return httpx.Response(
                200, json={"sha": remote.base_sha, "tree": {"sha": remote.base_tree}}
            )
        response = remote.handle(request)
        if request.method == "POST":
            remote.rows = [pull(selected)]
            return httpx.Response(201, json=remote.rows[0])
        return response

    monkeypatch.setattr(
        "checkedflow.github_drafts.httpx.Client",
        lambda **kw: original(**kw, transport=httpx.MockTransport(handle)),
    )
    adapter = Drafts(
        "owner/fixture",
        42,
        "owner",
        Token("fixture-secret"),
        tmp_path / "journal.sqlite",
        enabled=True,
    )
    return adapter, remote, selected, base, raw, contract


def test_bound_dispatch_and_read_reconciliation(bound):
    adapter, remote, selected, base, raw, contract = bound
    assert adapter.dispatch_patch(selected, base, raw, contract) == Outcome("confirmed", 7)
    assert adapter.dispatch_patch(selected, base, raw, contract) == Outcome("confirmed", 7)
    assert adapter.reconcile_patch(selected, base, raw, contract) == Outcome("confirmed", 7)
    assert remote.posts == 1
    remote.base_tree = "0" * 40
    for call in (adapter.dispatch_patch, adapter.reconcile_patch):
        with pytest.raises(Failure, match="base commit differs"):
            call(selected, base, raw, contract)
    assert remote.posts == 1


def test_byte_bound_dispatch_forwards_final_guard(bound):
    adapter, remote, selected, base, raw, contract = bound
    calls = []

    def reject(actual):
        calls.append(actual)
        assert remote.requests[-1].url.path.endswith("/pulls")
        raise Failure("STOPPED", "fixture emergency stop")

    assert adapter.dispatch_patch(selected, base, raw, contract, before_send=reject) == Outcome(
        "unknown"
    )
    assert calls == [selected] and remote.posts == 0
    assert adapter.dispatch_patch(selected, base, raw, contract) == Outcome("unknown")
    assert remote.posts == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository", "other/repository"),
        ("base_commit", "9" * 40),
        ("patch", "9" * 64),
        ("result", "9" * 64),
        ("git_tree", "9" * 40),
    ],
)
def test_unbound_plan_rejects_before_io(bound, field, value):
    adapter, remote, selected, base, raw, contract = bound
    with pytest.raises(Failure):
        adapter.dispatch_patch(replace(selected, **{field: value}), base, raw, contract)
    assert not remote.requests and not adapter.journal.exists()


def test_permission_bytes_and_disabled_provider(bound):
    adapter, remote, selected, base, raw, contract = bound
    with pytest.raises(Failure, match="AUTHORITY"):
        adapter.dispatch_patch(selected, base, raw, replace(contract, allow_draft_pr=False))
    with pytest.raises(Failure, match="patch bytes differ"):
        adapter.dispatch_patch(selected, base, raw + b" ", contract)
    with pytest.raises(Failure, match="base tree differs"):
        adapter.dispatch_patch(selected, Tree((("other.py", b""),)), raw, contract)
    adapter.enabled = False
    for call in (adapter.dispatch_patch, adapter.reconcile_patch):
        with pytest.raises(Failure, match="DISABLED"):
            call(selected, base, raw, contract)
    assert not remote.requests and not adapter.journal.exists()


@pytest.mark.parametrize("field", ["base_sha", "base_tree", "tree", "parent", "branch"])
def test_remote_binding_failure_never_sends(bound, field):
    adapter, remote, selected, base, raw, contract = bound
    setattr(remote, field, "9" * 40)
    with pytest.raises(Failure):
        adapter.dispatch_patch(selected, base, raw, contract)
    assert remote.posts == 0
