"""Supervised provider dispatch with real signed state, SQLite evidence and fixed HTTP fixtures."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from importlib.resources import files
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from test_github_drafts import pull
from test_repository_reuse import fixture as reuse_fixture
from test_work_effects import Harness

from checkedflow.core.values import Failure
from checkedflow.dispatch_watchdog import Watchdog
from checkedflow.effect_dispatch import Dispatcher, Policy
from checkedflow.git_tree import tree_id
from checkedflow.github_drafts import Drafts, Outcome, Token
from checkedflow.github_effects import Intent, reserved_plan
from checkedflow.repository_reuse import contract_digest, decode_tree, prepare
from checkedflow.wire import digest, dumps, loads


@pytest.fixture
def setup(tmp_path, monkeypatch):
    h, contract, inputs, store, access = reuse_fixture(tmp_path, harness=Harness(), draft=True)
    tree = prepare(h.runtime.state, h.candidate, contract, inputs, store, access=access).tree
    intent = Intent(
        contract.repository,
        42,
        "main",
        contract.base_commit,
        "b" * 40,
        tree_id(tree),
        contract.result_tree,
        contract_digest(contract),
        contract.patch_digest,
    )
    policy_record = {
        "profile": "checkedflow/effect-policy/v1",
        "chain": h.runtime.state.chain,
        "mission": "m",
        "repository": intent.repository,
        "repository_id": 42,
        "actor": "owner",
        "executor": "effects",
        "revision": 1,
        "enabled": True,
        "intents": [intent.digest],
    }
    path = tmp_path / "policy.json"
    path.write_bytes(dumps(policy_record))
    ticket, _ = h.send(
        "budget.reserve", {"phase": "execute", "target": intent.digest, "ceiling": 10}
    )
    h.effect, _ = h.send(
        "effect.prepare",
        {
            "candidate": h.candidate,
            "ticket": ticket,
            "intent": intent.digest,
            "policy": digest(policy_record),
            "executor": "effects",
            "revision": 1,
            "expires": contract.deadline_height,
            "lease_blocks": 20,
        },
    )
    h.action("authorize")
    h.action("reserve")
    f = SimpleNamespace(
        h=h,
        intent=intent,
        contract=contract,
        inputs=inputs,
        store=store,
        path=path,
        policy_record=policy_record,
        now=100,
        calls=[],
        hook=lambda: None,
    )
    watch = Watchdog(
        lambda: h.runtime.state,
        chain=h.runtime.state.chain,
        mission="m",
        max_read_age_ns=10,
        max_stall_ns=20,
        clock=lambda: f.now,
    )
    watch.poll()
    h.runtime.tick(h.runtime.state.height + 1)
    watch.poll()
    provider = Drafts(
        intent.repository,
        42,
        "owner",
        Token("fixture-secret"),
        tmp_path / "provider.sqlite",
        enabled=True,
    )
    selected = reserved_plan(
        h.runtime.state,
        h.effect,
        intent,
        contract,
        executor="effects",
        revision=1,
        policy=digest(policy_record),
    )
    base_tree = tree_id(decode_tree(store.get(inputs.base, access=access)))

    def request(method, suffix, **kwargs):
        f.calls.append((method, suffix))
        if method == "POST":
            assert suffix == "/pulls" and kwargs["payload"]["draft"] is True
            return pull(selected)
        if suffix == "":
            return {"id": 42, "full_name": intent.repository, "archived": False}
        if suffix == "/actions/permissions":
            return {"enabled": False}
        if suffix.startswith("/git/ref/heads/"):
            return {
                "object": {
                    "type": "commit",
                    "sha": intent.base_commit if suffix.endswith("/main") else intent.head_commit,
                }
            }
        if suffix == "/git/commits/" + intent.base_commit:
            return {"sha": intent.base_commit, "tree": {"sha": base_tree}}
        if suffix == "/git/commits/" + intent.head_commit:
            return {
                "sha": intent.head_commit,
                "tree": {"sha": intent.git_tree},
                "parents": [{"sha": intent.base_commit}],
            }
        assert suffix == "/pulls"
        f.hook()
        return []

    monkeypatch.setattr(provider, "_request", request)
    f.provider, f.watch = provider, watch
    f.dispatcher = Dispatcher(
        provider, watch, Policy(path), store, access, executor="effects", revision=1
    )
    f.run = lambda: f.dispatcher.dispatch(h.effect, intent, contract, inputs)
    return f


def test_verified_reservation_and_current_evidence_dispatch_once(setup):
    f = setup
    schema = loads(files("checkedflow").joinpath("data/effect-policy.schema.json").read_bytes())
    Draft202012Validator(schema).validate(f.policy_record)
    assert f.run() == Outcome("confirmed", 7)
    assert f.run() == Outcome("confirmed", 7)
    assert sum(method == "POST" for method, _ in f.calls) == 1
    assert f.h.current.status == "dispatch_reserved"  # Provider observation is not a signed report.


@pytest.mark.parametrize("fault", ["stop", "pause", "stale", "policy", "missing", "expired"])
def test_loss_during_remote_preflight_inhibits_post_and_preserves_claim(setup, fault):
    f = setup

    def lose():
        if fault == "stop":
            f.watch.stop()
        elif fault == "pause":
            f.h.send("mission.pause", {})
        elif fault == "stale":
            f.now += 21
        elif fault == "policy":
            f.path.write_bytes(dumps(f.policy_record | {"enabled": False}))
        elif fault == "expired":
            f.h.runtime.tick(f.h.current.until)
        else:
            with closing(sqlite3.connect(f.store.path)) as db:
                db.execute(
                    "DELETE FROM artifact_objects WHERE digest=?", (f.inputs.evidence[0].digest,)
                )
                db.commit()

    f.hook = lose
    assert f.run() == Outcome("unknown")
    assert all(method != "POST" for method, _ in f.calls)
    with f.provider._db() as db:
        assert db.execute("SELECT status FROM operations").fetchone() == ("unknown",)


@pytest.mark.parametrize(
    "change",
    [
        {"enabled": False},
        {"enabled": 1},
        {"profile": "future"},
        {"chain": "other"},
        {"mission": "other"},
        {"repository": "other/repo"},
        {"repository_id": True},
        {"repository_id": 99},
        {"actor": "other"},
        {"executor": "standby"},
        {"revision": 2},
        {"intents": []},
        {"intents": ["bad"]},
        {"intents": ["f" * 64, "0" * 64]},
        {"extra": 0},
    ],
)
def test_policy_denial_precedes_provider_io(setup, change):
    f = setup
    f.path.write_bytes(dumps(f.policy_record | change))
    with pytest.raises(Failure):
        f.run()
    assert not f.calls and not f.provider.journal.exists()


@pytest.mark.parametrize("raw", [None, b" " * 16385, b'{"profile":1,"profile":2}'])
def test_unavailable_or_malformed_policy_is_closed(setup, raw):
    f = setup
    if raw is None:
        f.path.unlink()
    else:
        f.path.write_bytes(raw)
    with pytest.raises(Failure):
        f.run()
    assert not f.calls


def test_policy_digest_requires_renewed_quorum_approval(setup):
    f = setup
    f.path.write_bytes(dumps(f.policy_record | {"intents": sorted([f.intent.digest, "0" * 64])}))
    with pytest.raises(Failure, match="BINDING"):
        f.run()
    assert not f.calls


def test_local_disable_and_wrong_owner_fail_before_provider_io(setup):
    f = setup
    f.provider.enabled = False
    with pytest.raises(Failure, match="DISABLED"):
        f.run()
    assert not f.calls
    f.provider.enabled = True
    f.dispatcher.revision = 2
    with pytest.raises(Failure, match="POLICY"):
        f.run()
    assert not f.calls


def test_changed_bytes_on_second_read_are_rejected(setup, monkeypatch):
    f = setup
    original = f.store.get
    count = 0

    def read(ref, *, access):
        nonlocal count
        if ref == f.inputs.base:
            count += 1
            if count == 2:
                return b"corrupt"
        return original(ref, access=access)

    monkeypatch.setattr(f.store, "get", read)
    with pytest.raises(Failure, match="INTEGRITY"):
        f.run()
    assert not f.calls


@pytest.mark.parametrize("fault", ["policy", "evidence", "clock"])
def test_state_policy_and_freshness_rechecked_after_artifact_reads(setup, monkeypatch, fault):
    f = setup
    original = f.store.get
    changed = False

    def read(ref, *, access):
        nonlocal changed
        if not changed:
            changed = True
            if fault == "policy":
                f.path.write_bytes(
                    dumps(f.policy_record | {"intents": sorted([f.intent.digest, "0" * 64])})
                )
            elif fault == "evidence":
                f.h.observation(3)
            else:
                f.now += 21
        return original(ref, access=access)

    monkeypatch.setattr(f.store, "get", read)
    with pytest.raises(Failure):
        f.run()
    assert not f.calls


def test_final_arguments_cannot_be_changed_by_provider(setup, monkeypatch):
    f = setup

    def dispatch(plan, base, patch, contract, *, before_send):
        before_send(replace(plan, authorization="f" * 64))
        pytest.fail("changed arguments reached sending")

    monkeypatch.setattr(f.provider, "dispatch_patch", dispatch)
    with pytest.raises(Failure, match="BINDING"):
        f.run()
