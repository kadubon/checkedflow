"""Protocol tests replace only the isolation boundary, never execute candidate Python."""

import hashlib
import json
from dataclasses import replace
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from patch_fixture import invoice
from test_repository_patch import target

from checkedflow.core.values import Failure
from checkedflow.domains.repository_patch import digest_bytes
from checkedflow.repository_execution import CHECKER_DIGEST, WRAPPER, inventory, observe_patch
from checkedflow.runner import Result
from checkedflow.wire import dumps, loads


def execution_target():
    base, patch, contract = target()
    cases = dumps(
        {
            "version": "repository-cases/v1",
            "path": "src/pricing.py",
            "function": "total",
            "cases": [{"case": "two-items", "input": [25, 75], "output": 100}],
        }
    )
    return (
        base,
        patch,
        replace(contract, checker_digest=CHECKER_DIGEST, test_inventory_digest=digest_bytes(cases)),
        cases,
    )


def boundary(monkeypatch, result):
    calls = []

    class Runner:
        def __init__(self, image, *, limits):
            calls.append((image, limits))

        def run_tree(self, argv, tree, request):
            calls.append((argv, tree, request))
            return result

    monkeypatch.setattr("checkedflow.repository_execution.GVisorRunner", Runner)
    return calls


def test_exact_bindings_and_external_expected_values(monkeypatch):
    base, patch, contract, cases = execution_target()
    result = Result(
        "reported", 0, b'{"results":[{"case":"two-items","output":100}]}', b"", "completed"
    )
    calls = boundary(monkeypatch, result)
    observation = observe_patch(base, patch, contract, cases, height=100)
    assert observation.case_match is True and observation.reason == "cases_match"
    assert observation.result_tree == contract.result_tree
    assert observation.stdout_digest == digest_bytes(result.stdout)
    assert calls[0][0] == contract.image
    assert calls[0][1].seconds == contract.cpu_seconds
    assert calls[0][1].memory_bytes == contract.memory_bytes
    assert calls[0][1].output_bytes == contract.output_bytes
    argv, tree, request = calls[1]
    assert argv == ("python", "-I", "-B", "-c", WRAPPER)
    assert tree.digest == contract.result_tree
    assert request["cases"] == [{"case": "two-items", "input": [25, 75]}]


@pytest.mark.parametrize(
    "change,code",
    [
        ({"checker_digest": "f" * 64}, "BINDING"),
        ({"test_inventory_digest": "f" * 64}, "BINDING"),
        ({"base_tree": "f" * 64}, "BINDING"),
        ({"deadline_height": 1}, "EXPIRED"),
    ],
)
def test_reject_before_sandbox(monkeypatch, change, code):
    base, patch, contract, cases = execution_target()
    calls = boundary(monkeypatch, Result("unknown", -1, b"", b"", "timeout"))
    with pytest.raises(Failure, match=code):
        observe_patch(base, patch, replace(contract, **change), cases, height=2)
    assert calls == []


@pytest.mark.parametrize(
    "result,match,reason",
    [
        (Result("unknown", -1, b"", b"", "timeout"), None, "timeout"),
        (Result("reported", 0, b'{"passed":true}', b"", "completed"), None, "invalid_output"),
        (
            Result(
                "reported", 0, b'{"results":[{"case":"two-items","output":true}]}', b"", "completed"
            ),
            False,
            "cases_differ",
        ),
    ],
)
def test_unknown_and_mismatch_are_separate(monkeypatch, result, match, reason):
    base, patch, contract, cases = execution_target()
    boundary(monkeypatch, result)
    observed = observe_patch(base, patch, contract, cases, height=1)
    assert observed.case_match is match and observed.reason == reason


def test_inventory_rejects_unsafe_ambiguous_or_empty_input():
    _, _, _, raw = execution_target()
    for field, value in [
        ("version", "future"),
        ("path", "../escape.py"),
        ("path", "module.txt"),
        ("function", "__import__"),
        ("cases", []),
    ]:
        plan = loads(raw)
        plan[field] = value
        with pytest.raises(Failure):
            inventory(dumps(plan))
    plan = loads(raw)
    plan["cases"] *= 2
    with pytest.raises(Failure, match="duplicate"):
        inventory(dumps(plan))
    with pytest.raises(Failure, match="LIMIT"):
        inventory(b" " * 262145)


def test_missing_entry_is_rejected_before_launch(monkeypatch):
    base, patch, contract, raw = execution_target()
    plan = loads(raw)
    plan["path"] = "missing.py"
    raw = dumps(plan)
    calls = boundary(monkeypatch, Result("unknown", -1, b"", b"", "timeout"))
    with pytest.raises(Failure, match="NOT_FOUND"):
        observe_patch(
            base, patch, replace(contract, test_inventory_digest=digest_bytes(raw)), raw, height=1
        )
    assert calls == []


def test_fixture_license_inventory_and_git_identity():
    resource = files("checkedflow").joinpath("data")
    fixture = json.loads(resource.joinpath("invoice-fixture.json").read_text())
    assert fixture["license"] == "Apache-2.0"
    assert "Apache License" in fixture["files"]["LICENSE"]

    def git_object(kind, body):
        return hashlib.sha1(
            kind.encode() + b" " + str(len(body)).encode() + b"\0" + body,
            usedforsecurity=False,
        ).digest()

    modules = b"".join(
        b"100644 " + path.split("/")[1].encode() + b"\0" + git_object("blob", body.encode())
        for path, body in sorted(fixture["files"].items())
        if path.startswith("shop/")
    )
    tree = git_object(
        "tree",
        b"100644 LICENSE\0"
        + git_object("blob", fixture["files"]["LICENSE"].encode())
        + b"40000 shop\0"
        + git_object("tree", modules),
    )
    commit = fixture["git_commit_object"].encode()
    assert commit.startswith(b"tree " + tree.hex().encode() + b"\n")
    assert git_object("commit", commit).hex() == fixture["base_commit"]
    base, patch, contract, cases = invoice("python@sha256:" + "b" * 64)
    request, expected = inventory(cases)
    assert request["path"] in dict(base.files) and len(expected) == 4
    assert contract.patch_digest == digest_bytes(patch)
    schema = json.loads(resource.joinpath("repository-cases.schema.json").read_text())
    validator = Draft202012Validator(schema)
    validator.validate(loads(cases))
    bad = loads(cases)
    bad["function"] += "\n"
    assert list(validator.iter_errors(bad))


def test_observation_binds_receiver_and_whole_contract(monkeypatch):
    base, patch, contract, cases = execution_target()
    boundary(monkeypatch, Result("unknown", -1, b"", b"", "cleanup_unknown"))
    first = observe_patch(base, patch, contract, cases, height=10)
    second = observe_patch(base, patch, replace(contract, receiver="different"), cases, height=10)
    assert first.contract_digest != second.contract_digest
    assert first.height == 10 and first.case_match is None
