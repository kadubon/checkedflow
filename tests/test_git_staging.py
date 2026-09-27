"""Exact staged objects, finite provider writes and conservative interrupted recovery."""

import os
import subprocess
from dataclasses import replace
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from test_effect_supervisor import Executor
from test_github_drafts import pull

from checkedflow.core.values import Failure
from checkedflow.domains.repository_patch import Tree, apply_patch
from checkedflow.git_staging import staging_commit
from checkedflow.git_tree import tree_id
from checkedflow.github_drafts import Outcome
from checkedflow.repository_reuse import decode_tree
from checkedflow.wire import document, dumps

POSTS = ["/git/trees", "/git/commits", "/git/refs", "/pulls"]


class Provider:
    def __init__(self, e, monkeypatch):
        self.e = e
        self.plan = e.f.intent._plan(e.f.h.current.operation, e.f.h.current.authorization)
        self.posts = []
        self.tree = self.commit = self.branch = False
        self.row = None
        self.fail = ""
        self.when = "before"
        self.hook = lambda: None
        original = e.f.provider._request

        def request(method, suffix, **kwargs):
            if method == "GET":
                if suffix == "/git/ref/heads/" + self.plan.branch:
                    assert self.branch
                if suffix == "/git/commits/" + self.plan.head_commit:
                    assert self.commit
                result = original(method, suffix, **kwargs)
                return [self.row] if suffix == "/pulls" and self.row else result
            assert method == "POST" and suffix in POSTS
            self.posts.append(suffix)
            e.f.calls.append((method, suffix))
            self.hook()
            if self.fail == suffix and self.when == "before":
                raise OSError("private lost connection")
            payload = kwargs["payload"]
            if suffix == "/git/trees":
                assert set(payload) == {"tree"}  # Complete source; no inherited remote files.
                assert all(
                    row["mode"] == "100644" and row["type"] == "blob" for row in payload["tree"]
                )
                tree = Tree(
                    tuple((row["path"], row["content"].encode()) for row in payload["tree"])
                )
                assert tree_id(tree) == self.plan.git_tree
                self.tree = True
                result = {"sha": self.plan.git_tree}
            elif suffix == "/git/commits":
                assert self.tree
                base = decode_tree(e.f.store.get(e.f.inputs.base, access=e.f.dispatcher.access))
                patch = e.f.store.get(e.f.inputs.patch, access=e.f.dispatcher.access)
                expected = staging_commit(e.f.contract, apply_patch(base, patch, e.f.contract))
                assert payload == expected[1]
                self.commit = True
                result = {"sha": self.plan.head_commit}
            elif suffix == "/git/refs":
                assert self.commit
                if self.branch:
                    raise Failure("OUTCOME_UNKNOWN", "reference already exists; no overwrite")
                assert payload == {
                    "ref": "refs/heads/" + self.plan.branch,
                    "sha": self.plan.head_commit,
                }
                self.branch = True
                result = {
                    "ref": payload["ref"],
                    "object": {"type": "commit", "sha": self.plan.head_commit},
                }
            else:
                assert (
                    self.branch
                    and payload["draft"] is True
                    and payload["maintainer_can_modify"] is False
                )
                self.row = pull(self.plan)
                result = self.row
            if self.fail == suffix and self.when == "after":
                raise OSError("private lost committed reply")
            if self.fail == suffix and self.when == "wrong":
                return {
                    "sha": "0" * 40,
                    "ref": "refs/heads/wrong",
                    "object": {"type": "tag", "sha": "0" * 40},
                }
            return result

        monkeypatch.setattr(e.f.provider, "_request", request)


def setup(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch, staging=True)
    return e, Provider(e, monkeypatch)


def test_deterministic_commit_matches_independent_git_oracle(tmp_path, monkeypatch):
    e, _ = setup(tmp_path, monkeypatch)
    base = decode_tree(e.f.store.get(e.f.inputs.base, access=e.f.dispatcher.access))
    patch = e.f.store.get(e.f.inputs.patch, access=e.f.dispatcher.access)
    tree = apply_patch(base, patch, e.f.contract)
    head, payload = staging_commit(e.f.contract, tree)
    subprocess.run(["git", "init", "-q", str(tmp_path / "oracle")], check=True)
    environment = os.environ | {
        "GIT_AUTHOR_NAME": "CheckedFlow",
        "GIT_COMMITTER_NAME": "CheckedFlow",
        "GIT_AUTHOR_EMAIL": "checkedflow@example.invalid",
        "GIT_COMMITTER_EMAIL": "checkedflow@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    body = (
        f"tree {payload['tree']}\nparent {e.f.contract.base_commit}\n"
        "author CheckedFlow <checkedflow@example.invalid> 946684800 +0000\n"
        "committer CheckedFlow <checkedflow@example.invalid> 946684800 +0000\n\n"
        + payload["message"]
    ).encode()
    actual = (
        subprocess.run(
            ["git", "hash-object", "-t", "commit", "--stdin"],
            input=body,
            cwd=tmp_path / "oracle",
            env=environment,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    assert actual == head == e.f.intent.head_commit
    with pytest.raises(Failure, match="BINDING"):
        staging_commit(replace(e.f.contract, result_tree="a" * 64), tree)


def test_governed_staging_reporting_and_restart_never_repeat_writes(tmp_path, monkeypatch):
    e, provider = setup(tmp_path, monkeypatch)
    assert e.step() == "observed"
    assert provider.posts == POSTS and e.f.h.current.number == 7
    assert e.read().budget.spent == 40 and len(e.sent) == 2
    e.supervisor = e.reopen()
    assert e.step() == "observed" and provider.posts == POSTS


@pytest.mark.parametrize("suffix", POSTS)
@pytest.mark.parametrize("when", ["before", "after", "wrong"])
def test_each_ambiguous_stage_stops_and_never_resends(tmp_path, monkeypatch, suffix, when):
    e, provider = setup(tmp_path, monkeypatch)
    provider.fail, provider.when = suffix, when
    assert e.step() == "unknown"
    assert provider.posts == POSTS[: POSTS.index(suffix) + 1]
    assert b"private" not in e.evidence()
    e.supervisor = e.reopen()
    assert e.step() == "unknown" and len(provider.posts) == POSTS.index(suffix) + 1
    assert e.f.h.current.fence == 1 and e.read().budget.spent == 40


@pytest.mark.parametrize("position", range(4))
def test_every_write_rechecks_current_consensus_before_sending(tmp_path, monkeypatch, position):
    e, provider = setup(tmp_path, monkeypatch)
    original = e.f.dispatcher._checked
    checks = []

    def check(*args):
        checks.append(1)
        if len(checks) == position + 2:
            e.f.h.send("mission.pause", {})
        return original(*args)

    monkeypatch.setattr(e.f.dispatcher, "_checked", check)
    assert e.step() == "unknown" and provider.posts == POSTS[:position]


def test_existing_branch_is_never_overwritten_or_followed_by_pr(tmp_path, monkeypatch):
    e, provider = setup(tmp_path, monkeypatch)
    provider.branch = True
    assert e.step() == "unknown" and provider.posts == POSTS[:3]
    assert provider.row is None


def test_staging_requires_new_explicit_policy_before_reservation(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    e.f.dispatcher.staging = True
    with pytest.raises(Failure, match="POLICY"):
        e.step()
    assert not e.sent and not e.f.calls and e.f.h.current.status == "authorized"


def test_crash_after_staging_claim_never_runs_again(tmp_path, monkeypatch):
    e, provider = setup(tmp_path, monkeypatch)

    def crash():
        raise SystemExit("fixture process death")

    provider.hook = crash
    with pytest.raises(SystemExit):
        e.step()
    e.supervisor = e.reopen()
    assert e.step() == "unknown" and provider.posts == ["/git/trees"]


def test_changed_staging_mode_is_not_adopted_during_recovery(tmp_path, monkeypatch):
    e, _ = setup(tmp_path, monkeypatch)
    e.behavior = "before_effect.reserve"
    with pytest.raises(Failure):
        e.step()
    e.behavior = "normal"
    e.coordinator.retransmit()
    e.f.dispatcher.staging = False
    with pytest.raises(Failure, match="BINDING"):
        e.step()


def test_staging_recovery_reuses_original_provider_receipt(tmp_path, monkeypatch):
    e, provider = setup(tmp_path, monkeypatch)
    assert e.step() == "observed"
    base = decode_tree(e.f.store.get(e.f.inputs.base, access=e.f.dispatcher.access))
    patch = e.f.store.get(e.f.inputs.patch, access=e.f.dispatcher.access)
    assert e.f.provider.stage_and_dispatch_patch(
        provider.plan,
        base,
        patch,
        e.f.contract,
        before_send=lambda p: pytest.fail("receipt cannot send"),
    ) == Outcome("confirmed", 7)
    assert provider.posts == POSTS


@pytest.mark.parametrize("flag", [False, 1, "true", None])
def test_staging_policy_is_explicit_and_strict(tmp_path, monkeypatch, flag):
    e, provider = setup(tmp_path, monkeypatch)
    record = e.f.policy_record | {"staging": flag}
    e.f.path.write_bytes(dumps(record))
    with pytest.raises(Failure, match="POLICY|SHAPE"):
        e.step()
    assert not e.sent and not provider.posts


def test_packaged_staging_policy_schema(tmp_path, monkeypatch):
    e, _ = setup(tmp_path, monkeypatch)
    schema = document(
        files("checkedflow").joinpath("data/effect-staging-policy.schema.json").read_bytes()
    )
    validator = Draft202012Validator(schema)
    validator.validate(e.f.policy_record)
    assert list(validator.iter_errors(e.f.policy_record | {"staging": 1}))
    assert list(validator.iter_errors(e.f.policy_record | {"extra": True}))


def test_existing_exact_draft_is_observed_without_staging(tmp_path, monkeypatch):
    e, provider = setup(tmp_path, monkeypatch)
    provider.branch = provider.commit = True
    provider.row = pull(provider.plan)
    assert e.step() == "observed" and not provider.posts


def test_different_planned_head_is_rejected_before_any_provider_access(tmp_path, monkeypatch):
    e = Executor(tmp_path, monkeypatch)
    plan = e.f.intent._plan(e.f.h.current.operation, e.f.h.current.authorization)
    base = decode_tree(e.f.store.get(e.f.inputs.base, access=e.f.dispatcher.access))
    patch = e.f.store.get(e.f.inputs.patch, access=e.f.dispatcher.access)
    with pytest.raises(Failure, match="BINDING"):
        e.f.provider.stage_and_dispatch_patch(
            plan, base, patch, e.f.contract, before_send=lambda p: None
        )
    assert not e.f.calls
