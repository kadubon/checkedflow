"""Resolve current quorum-bound intent into provider arguments without provider I/O."""

from dataclasses import replace
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from test_repository_patch import target
from test_work_effects import Harness

from checkedflow.core.values import Failure
from checkedflow.domains.repository_patch import apply_patch
from checkedflow.git_tree import tree_id
from checkedflow.github_effects import Intent, decode_intent, reserved_plan
from checkedflow.repository_reuse import contract_digest
from checkedflow.wire import dumps, loads


def fixture():
    base, patch, contract = target()
    contract = replace(contract, allow_draft_pr=True)
    intent = Intent(
        contract.repository,
        42,
        "main",
        contract.base_commit,
        "b" * 40,
        tree_id(apply_patch(base, patch, contract)),
        contract.result_tree,
        contract_digest(contract),
        contract.patch_digest,
    )
    h = Harness()
    h.prepare_checks(target=intent.target)
    h.admit(target=intent.target, artifact=intent.result)
    for index in range(4):
        h.observation(index)
    ticket, _ = h.send(
        "budget.reserve", {"phase": "execute", "target": intent.digest, "ceiling": 10}
    )
    h.effect, _ = h.send(
        "effect.prepare",
        {
            "candidate": h.candidate,
            "ticket": ticket,
            "intent": intent.digest,
            "policy": "e" * 64,
            "executor": "effects",
            "revision": 1,
            "expires": 90,
            "lease_blocks": 20,
        },
    )
    h.action("authorize")
    h.action("reserve")
    return h, intent, contract


def resolve(h, intent, contract, **changes):
    return reserved_plan(
        h.runtime.state,
        h.effect,
        intent,
        contract,
        **({"executor": "effects", "revision": 1, "policy": "e" * 64} | changes),
    )


def test_exact_reserved_plan_binds_candidate_intent_and_authorization():
    h, intent, contract = fixture()
    selected = resolve(h, intent, contract)
    assert selected.operation == h.current.operation
    assert selected.authorization == h.current.authorization
    assert selected.patch == contract.patch_digest and selected.result == contract.result_tree
    assert selected.git_tree == intent.git_tree
    assert decode_intent(dumps(intent.record())) == intent
    schema = loads(
        files("checkedflow").joinpath("data/github-effect-intent.schema.json").read_bytes()
    )
    Draft202012Validator(schema).validate(intent.record())
    for name, value in (
        ("repository", "other/repo"),
        ("repository_id", 43),
        ("head_commit", "c" * 40),
        ("git_tree", "c" * 40),
        ("base_branch", "other"),
        ("target", "0" * 64),
        ("patch", "0" * 64),
    ):
        with pytest.raises(Failure, match="BINDING"):
            resolve(h, replace(intent, **{name: value}), contract)
    with pytest.raises(Failure, match="BINDING"):
        resolve(h, intent, replace(contract, receiver="other"))
    for changes in ({"executor": "standby"}, {"revision": 2}, {"policy": "0" * 64}):
        with pytest.raises(Failure):
            resolve(h, intent, contract, **changes)
    with pytest.raises(Failure, match="NOT_FOUND"):
        reserved_plan(
            h.runtime.state,
            "missing",
            intent,
            contract,
            executor="effects",
            revision=1,
            policy="e" * 64,
        )
    h.send("mission.pause", {})
    with pytest.raises(Failure, match="STATE"):
        resolve(h, intent, contract)


def test_unknown_and_late_reservations_never_resolve_for_sending():
    h, intent, contract = fixture()
    h.runtime.tick(h.current.until)
    with pytest.raises(Failure):
        resolve(h, intent, contract)


def test_intent_parser_is_closed_bounded_and_does_not_grant_authority():
    _, intent, _ = fixture()
    for record in (
        intent.record() | {"extra": True},
        intent.record() | {"version": "unknown"},
        intent.record() | {"repository_id": True},
        intent.record() | {"target": "bad"},
    ):
        with pytest.raises(Failure):
            decode_intent(dumps(record))
    with pytest.raises(Failure, match="LIMIT"):
        decode_intent(b" " * 8193)
    with pytest.raises(Failure):
        decode_intent(b'{"version":"a","version":"b"}')
