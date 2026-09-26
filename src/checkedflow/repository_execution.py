"""Contract-bound repository observations; these are not adoption or effect authority."""

import re
from dataclasses import asdict, dataclass
from importlib.resources import files
from typing import cast

from checkedflow.core.values import (
    JSON,
    Failure,
    Object,
    array,
    fields,
    integer,
    obj,
    require,
    text,
)
from checkedflow.domains.repository_patch import (
    MAX_CASES,
    MAX_FILE_BYTES,
    Contract,
    Tree,
    apply_patch,
    check_outputs,
    digest_bytes,
    normalized_path,
)
from checkedflow.runner import GVisorRunner, Limits
from checkedflow.wire import document, dumps

# Only the sandbox interprets this code or any repository source. Expected outputs
# remain outside the sandbox. A candidate can still fabricate its own output report.
WRAPPER = """import json, runpy, sys
request = json.load(sys.stdin)
sys.path.insert(0, '/work')
namespace = runpy.run_path('/work/' + request['path'])
function = namespace[request['function']]
results = [{'case': case['case'], 'output': function(case['input'])}
           for case in request['cases']]
sys.stdout.write(json.dumps({'results': results}, separators=(',', ':'), allow_nan=False))
"""
CHECKER_DIGEST = digest_bytes(
    dumps(
        {
            "version": "checkedflow/repository-observer/v1",
            "wrapper_sha256": digest_bytes(WRAPPER.encode()),
            "comparison": "repository-patch/v1:ordered-canonical-json",
            "sources": {
                path: digest_bytes(files("checkedflow").joinpath(path).read_bytes())
                for path in (
                    "repository_execution.py",
                    "domains/repository_patch.py",
                    "wire.py",
                    "core/values.py",
                )
            },
        }
    )
)


def inventory(raw: bytes) -> tuple[Object, tuple[Object, ...]]:
    """Validate a bounded evaluator-owned inventory and separate inputs from answers."""
    require(len(raw) <= MAX_FILE_BYTES, "LIMIT", "inventory byte ceiling exceeded")
    plan = document(raw)
    fields(plan, "version path function cases")
    require(plan["version"] == "repository-cases/v1", "VERSION", "unsupported inventory")
    path = normalized_path(text(plan["path"]))
    require(path.endswith(".py"), "PATH", "Python entry module required")
    function = text(plan["function"], limit=80)
    require(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", function) is not None,
        "SHAPE",
        "explicit public function name required",
    )
    cases = array(plan["cases"], limit=MAX_CASES)
    require(bool(cases), "EVIDENCE", "nonempty inventory required")
    inputs: list[JSON] = []
    expected: list[Object] = []
    seen: set[str] = set()
    for value in cases:
        case = obj(value)
        fields(case, "case input output")
        name = text(case["case"], limit=128)
        require(name not in seen, "EVIDENCE", "duplicate case identifier")
        seen.add(name)
        inputs.append({"case": name, "input": case["input"]})
        expected.append({"case": name, "output": case["output"]})
    return {"path": path, "function": function, "cases": inputs}, tuple(expected)


@dataclass(frozen=True)
class Observation:
    """One bounded invocation, with unknown preserved and no implied quorum."""

    contract_digest: str
    height: int
    result_tree: str
    inventory_digest: str
    checker_digest: str
    image: str
    stdout_digest: str
    stderr_digest: str
    case_match: bool | None
    reason: str


def observe_patch(
    base: Tree, patch: bytes, contract: Contract, cases: bytes, *, height: int
) -> Observation:
    """Observe an already-authorized target using fresh trusted-node height.

    Administrative approval, budget reservation, lease/fence validation and freshness
    are caller obligations. This function does not submit, register, reuse or publish.
    No repository pytest/lint success or general correctness is inferred.
    """
    integer(height)
    require(height <= contract.deadline_height, "EXPIRED", "verification target expired")
    require(contract.checker_digest == CHECKER_DIGEST, "BINDING", "checker differs")
    require(digest_bytes(cases) == contract.test_inventory_digest, "BINDING", "inventory differs")
    request, expected = inventory(cases)
    tree = apply_patch(base, patch, contract)
    require(request["path"] in dict(tree.files), "NOT_FOUND", "entry module missing")
    runner = GVisorRunner(
        contract.image,
        limits=Limits(
            seconds=contract.cpu_seconds,
            memory_bytes=contract.memory_bytes,
            output_bytes=contract.output_bytes,
        ),
    )
    result = runner.run_tree(("python", "-I", "-B", "-c", WRAPPER), tree, request)
    matched: bool | None = None
    reason = result.reason
    if result.status == "reported":
        try:
            matched = check_outputs(result.stdout, expected, byte_limit=contract.output_bytes)
            reason = "cases_match" if matched else "cases_differ"
        except Failure:
            reason = "invalid_output"
    bound_contract = cast(Object, asdict(contract))
    bound_contract["allowed_paths"] = list(contract.allowed_paths)
    return Observation(
        digest_bytes(dumps(bound_contract)),
        height,
        tree.digest,
        contract.test_inventory_digest,
        CHECKER_DIGEST,
        contract.image,
        digest_bytes(result.stdout),
        digest_bytes(result.stderr),
        matched,
        reason,
    )
