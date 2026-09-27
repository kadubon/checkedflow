"""Snapshot-bound reuse tests; fixture observations never execute candidate code."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from importlib.resources import files
from io import BytesIO

import pytest
from jsonschema import Draft202012Validator
from patch_fixture import invoice
from test_work_acceptance import Harness

from checkedflow.artifacts import Access, LocalStore
from checkedflow.core.artifact import Reference
from checkedflow.core.values import Failure
from checkedflow.domains.repository_patch import Tree, digest_bytes
from checkedflow.repository_reuse import Inputs, contract_digest, decode_tree, prepare, tree_bytes
from checkedflow.wire import dumps, loads


def fixture(tmp_path, report_change=None, unknown_fourth=False, *, harness=None, draft=False):
    base, patch, contract, cases = invoice("python@sha256:" + "1" * 64)
    contract = replace(contract, allow_draft_pr=draft)
    target = contract_digest(contract)
    h = harness or Harness()
    h.prepare_checks(target)
    h.admit(target=target, artifact=contract.result_tree, expires=contract.deadline_height)
    store = LocalStore(tmp_path / "reuse.sqlite")
    access = Access("reviewer", frozenset({"m"}), frozenset({"read", "write"}))

    def put(raw, kind):
        ref = Reference(
            "sha256", digest_bytes(raw), len(raw), "application/json", kind, "m", target
        )
        store.put(ref, BytesIO(raw), access=access)
        return ref

    base_ref = put(tree_bytes(base), "source-tree")
    patch_ref = put(patch, "patch")
    inventory_ref = put(cases, "evidence")
    evidence = []
    for index in range(4 if unknown_fourth else 3):
        task, actor = h.checks[index], f"v{index}"
        h.send("task.lease", {"task": task}, actor)
        h.send("task.start", {"task": task, "fence": 1}, actor)
        unknown = unknown_fourth and index == 3
        report = {
            "contract_digest": target,
            "height": h.runtime.state.height,
            "result_tree": contract.result_tree,
            "inventory_digest": contract.test_inventory_digest,
            "checker_digest": contract.checker_digest,
            "image": contract.image,
            "stdout_digest": "d" * 64,
            "stderr_digest": "e" * 64,
            "case_match": None if unknown else True,
            "reason": "timeout" if unknown else "cases_match",
        }
        if report_change:
            report.update(report_change)
        ref = put(dumps(report), "evidence")
        evidence.append(ref)
        h.send(
            "task.finish",
            {
                "task": task,
                "fence": 1,
                "outcome": "unknown" if unknown else "reported",
                "evidence": ref.digest,
            },
            actor,
        )
        h.send(
            "artifact.attest",
            {
                "candidate": h.candidate,
                "task": task,
                "evidence": ref.digest,
                "verdict": "unknown" if unknown else "pass",
            },
            actor,
        )
    return h, contract, Inputs(base_ref, patch_ref, inventory_ref, tuple(evidence)), store, access


def test_exact_scope_reconstructs_available_patch_without_new_execution(tmp_path):
    h, contract, inputs, store, access = fixture(tmp_path, unknown_fourth=True)
    before = h.runtime.state_hash
    result = prepare(h.runtime.state, h.candidate, contract, inputs, store, access=access)
    assert result.tree.digest == contract.result_tree
    assert result.target == contract_digest(contract) and result.state_hash == before
    assert result.height == h.runtime.state.height and len(result.checked_objects) == 7
    assert decode_tree(tree_bytes(result.tree)) == result.tree
    assert h.runtime.state_hash == before and h.runtime.state.budget.spent == 40


@pytest.mark.parametrize(
    "change",
    [
        {"base_commit": "2" * 40},
        {"base_tree": "2" * 64},
        {"receiver": "another-receiver"},
        {"image": "python@sha256:" + "2" * 64},
        {"checker_digest": "2" * 64},
        {"test_inventory_digest": "2" * 64},
        {"lint_config_digest": "2" * 64},
    ],
)
def test_changed_context_requires_new_qualification(tmp_path, change):
    h, contract, inputs, store, access = fixture(tmp_path)
    with pytest.raises(Failure, match="requested use differs"):
        prepare(
            h.runtime.state, h.candidate, replace(contract, **change), inputs, store, access=access
        )


@pytest.mark.parametrize(
    "change",
    [
        {"contract_digest": "f" * 64},
        {"result_tree": "f" * 64},
        {"image": "wrong"},
        {"inventory_digest": "f" * 64},
        {"checker_digest": "f" * 64},
        {"height": 0},
        {"height": 999},
        {"case_match": 1},
        {"case_match": False},
        {"reason": "PASS"},
        {"stdout_digest": "bad"},
        {"stderr_digest": "bad"},
    ],
)
def test_signed_pass_does_not_hide_inconsistent_evidence(tmp_path, change):
    h, contract, inputs, store, access = fixture(tmp_path, report_change=change)
    with pytest.raises(Failure):
        prepare(h.runtime.state, h.candidate, contract, inputs, store, access=access)


@pytest.mark.parametrize("fault", ["missing", "corrupt", "outage"])
def test_old_acceptance_does_not_hide_lost_or_corrupt_bytes(tmp_path, fault):
    h, contract, inputs, store, access = fixture(tmp_path)
    prepare(h.runtime.state, h.candidate, contract, inputs, store, access=access)
    if fault == "outage":

        class Unavailable:
            def get(self, ref, *, access):
                raise OSError("storage unavailable")

        store = Unavailable()
    else:
        with closing(sqlite3.connect(store.path)) as db:
            if fault == "missing":
                db.execute(
                    "DELETE FROM artifact_objects WHERE digest=?", (inputs.evidence[0].digest,)
                )
            else:
                db.execute(
                    "UPDATE artifact_objects SET body=? WHERE digest=?",
                    (b"damaged", inputs.evidence[0].digest),
                )
            db.commit()
    with pytest.raises((Failure, OSError)):
        prepare(h.runtime.state, h.candidate, contract, inputs, store, access=access)


def test_authority_state_and_exact_reference_inventory(tmp_path):
    h, contract, inputs, store, access = fixture(tmp_path)
    for bad in (
        replace(inputs, evidence=inputs.evidence[:-1]),
        replace(inputs, evidence=inputs.evidence + inputs.evidence[:1]),
        replace(inputs, evidence=inputs.evidence * 5),
        replace(inputs, base=replace(inputs.base, scope="other")),
        replace(inputs, patch=replace(inputs.patch, manifest="a" * 64)),
        replace(inputs, base=replace(inputs.base, kind="patch")),
        replace(inputs, inventory=replace(inputs.inventory, length=262145)),
    ):
        with pytest.raises(Failure):
            prepare(h.runtime.state, h.candidate, contract, bad, store, access=access)
    denied = replace(access, permissions=frozenset())

    class Unreadable:
        def get(self, ref, *, access):
            pytest.fail("unauthorized storage read")

    with pytest.raises(Failure, match="AUTHORITY"):
        prepare(h.runtime.state, h.candidate, contract, inputs, Unreadable(), access=denied)
    with pytest.raises(Failure, match="NOT_FOUND"):
        prepare(h.runtime.state, "absent", contract, inputs, store, access=access)
    h.send("mission.pause", {})
    with pytest.raises(Failure, match="PAUSED"):
        prepare(h.runtime.state, h.candidate, contract, inputs, store, access=access)
    h.send("mission.resume", {})
    h.send("artifact.withdraw", {"candidate": h.candidate, "task": h.checks[0]}, "v0")
    with pytest.raises(Failure, match="ACCEPTANCE"):
        prepare(h.runtime.state, h.candidate, contract, inputs, store, access=access)


def test_untrusted_tree_bundle_cannot_create_paths_links_or_binary_files():
    assert decode_tree(tree_bytes(Tree((("empty.py", b""),)))) == Tree((("empty.py", b""),))
    schema = loads(files("checkedflow").joinpath("data/repository-tree.schema.json").read_bytes())
    Draft202012Validator(schema).validate(loads(tree_bytes(Tree((("empty.py", b""),)))))
    for value in [
        {"version": "wrong", "files": []},
        {"version": "repository-tree/v1", "files": [{"path": "../escape.py", "content": ""}]},
        {"version": "repository-tree/v1", "files": [{"path": "x.py", "content": 1}]},
        {
            "version": "repository-tree/v1",
            "files": [{"path": "x.py", "content": "", "link": "target"}],
        },
        {"version": "repository-tree/v1", "files": [{"path": "x.py", "content": "\u0000"}]},
    ]:
        with pytest.raises(Failure):
            decode_tree(dumps(value))
    with pytest.raises(Failure, match="LIMIT"):
        decode_tree(b" " * 4194305)


def test_provider_cannot_bypass_final_byte_integrity(tmp_path):
    h, contract, inputs, store, access = fixture(tmp_path)

    class CorruptProvider:
        def get(self, ref, *, access):
            raw = store.get(ref, access=access)
            return raw + b" " if ref.digest == inputs.evidence[0].digest else raw

    with pytest.raises(Failure, match="INTEGRITY"):
        prepare(h.runtime.state, h.candidate, contract, inputs, CorruptProvider(), access=access)
