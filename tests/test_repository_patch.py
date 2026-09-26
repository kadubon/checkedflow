"""Admission and independent-output checks; no candidate code is executed on the host."""

from dataclasses import replace

import pytest

from checkedflow.core.values import Failure
from checkedflow.domains.repository_patch import (
    Contract,
    Tree,
    apply_patch,
    check_outputs,
    digest_bytes,
    normalized_path,
)
from checkedflow.wire import dumps, loads


def target(*, path="src/pricing.py", content="def total(items): return sum(items)\n"):
    base = Tree(
        (
            ("src/pricing.py", b"def total(items): return len(items)\n"),
            ("tests/test_pricing.py", b""),
        )
    )
    raw = dumps(
        {
            "version": "repository-patch/v1",
            "changes": [
                {"path": path, "before": digest_bytes(base.files[0][1]), "content": content}
            ],
        }
    )
    result = Tree((("src/pricing.py", content.encode()), ("tests/test_pricing.py", b"")))
    contract = Contract(
        repository="fixtures/pricing",
        base_commit="a" * 40,
        base_tree=base.digest,
        result_tree=result.digest,
        patch_digest=digest_bytes(raw),
        image="python@sha256:" + "b" * 64,
        checker_digest="c" * 64,
        test_inventory_digest="d" * 64,
        lint_config_digest="e" * 64,
        receiver="fixture-user",
        allowed_paths=("src/pricing.py",),
        max_changed_files=1,
        max_patch_bytes=8192,
        cpu_seconds=5,
        memory_bytes=67108864,
        output_bytes=4096,
        deadline_height=100,
    )
    return base, raw, contract


def test_patch_binds_preimage_result_and_scope_without_executing():
    base, raw, contract = target()
    result = apply_patch(base, raw, contract)
    assert result.digest == contract.result_tree
    assert base.files[0][1] == b"def total(items): return len(items)\n"
    assert not contract.allow_draft_pr
    with pytest.raises(Failure, match="base tree differs"):
        apply_patch(result, raw, contract)
    with pytest.raises(Failure, match="patch bytes differ"):
        apply_patch(base, raw + b" ", contract)
    with pytest.raises(Failure, match="result tree differs"):
        apply_patch(base, raw, replace(contract, result_tree="f" * 64))
    with pytest.raises(Failure, match="ceiling"):
        apply_patch(base, raw, replace(contract, max_patch_bytes=1))


@pytest.mark.parametrize(
    "path",
    ["/tmp/a", "../a", "a/../b", "a\\b", "C:/a", "a//b", ".git/hooks/x", "a/CON.py", "a.", "é.py"],
)
def test_nonportable_or_escaping_paths_are_rejected(path):
    with pytest.raises(Failure):
        normalized_path(path)


@pytest.mark.parametrize(
    "files",
    [(("A.py", b""), ("a.py", b"")), (("a", b""), ("a/b.py", b"")), (("x.py", b"\x00"),)],
)
def test_tree_rejects_collisions_and_binary_content(files):
    with pytest.raises(Failure):
        Tree(files)


def test_candidate_cannot_change_tests_or_checker_controls():
    base, raw, contract = target(path="tests/test_pricing.py")
    with pytest.raises(Failure, match="not approved"):
        apply_patch(base, raw, contract)
    for path in [
        "tests/check.py",
        "conftest.py",
        "src/sitecustomize.py",
        "setup.py",
        "test_app.py",
    ]:
        with pytest.raises(Failure, match="protected"):
            replace(contract, allowed_paths=(path,))


def test_incorrect_output_and_forged_reports_cannot_assert_success():
    expected = ({"case": "sum", "output": 7}, {"case": "empty", "output": 0})
    assert check_outputs(dumps({"results": list(expected)}), expected, byte_limit=4096)
    assert not check_outputs(
        dumps({"results": [{"case": "sum", "output": 2}, expected[1]]}), expected, byte_limit=4096
    )
    for raw in [b'"PASS"', b'{"pass":true}', b'{"results":[]}', b'{"results":[],"results":[]}']:
        with pytest.raises(Failure):
            check_outputs(raw, expected, byte_limit=4096)
    with pytest.raises(Failure, match="nonempty"):
        check_outputs(b'{"results":[]}', (), byte_limit=4096)
    with pytest.raises(Failure, match="ceiling"):
        check_outputs(b" " * 50, expected, byte_limit=10)


def test_output_comparison_preserves_json_types_and_inventory():
    expected = ({"case": "one", "output": 1},)
    assert not check_outputs(
        b'{"results":[{"case":"one","output":true}]}', expected, byte_limit=4096
    )
    with pytest.raises(Failure, match="inventory"):
        check_outputs(b'{"results":[{"case":"two","output":1}]}', expected, byte_limit=4096)
    with pytest.raises(Failure):
        check_outputs(b'{"results":[{"case":"one","output":1.0}]}', expected, byte_limit=4096)


def test_deletion_is_bound_to_original_bytes_and_result_tree():
    base, raw, contract = target()
    patch = loads(raw)
    patch["changes"][0]["content"] = None
    raw = dumps(patch)
    expected = Tree((("tests/test_pricing.py", b""),))
    contract = replace(contract, patch_digest=digest_bytes(raw), result_tree=expected.digest)
    assert apply_patch(base, raw, contract) == expected
    patch["changes"][0]["before"] = "f" * 64
    changed = dumps(patch)
    with pytest.raises(Failure, match="preimage"):
        apply_patch(base, changed, replace(contract, patch_digest=digest_bytes(changed)))


def test_invalid_utf8_is_rejected_before_materialization():
    with pytest.raises(Failure) as failure:
        Tree((("src/app.py", b"\xff"),))
    assert failure.value.code == "UNICODE"
