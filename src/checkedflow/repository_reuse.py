"""Reconstruct an exact accepted patch from currently verified scoped bytes.

This is a snapshot-bound preparation result, not permission to execute or publish it.
The caller supplies its own validating node's state and enforces dispatch freshness.
"""

from dataclasses import asdict, dataclass
from typing import cast

from checkedflow.artifact_io import Access, ArtifactStore
from checkedflow.core.artifact import MAX_ARTIFACT_BYTES, Reference
from checkedflow.core.operational import State
from checkedflow.core.values import JSON, Object, array, fields, integer, obj, require, text
from checkedflow.core.work_acceptance import Candidate, fingerprint, status
from checkedflow.domains.repository_patch import (
    MAX_FILES,
    Contract,
    Tree,
    apply_patch,
    digest_bytes,
)
from checkedflow.operational_codec import decode, state_bytes
from checkedflow.repository_execution import CHECKER_DIGEST, inventory
from checkedflow.wire import document, dumps


@dataclass(frozen=True)
class Inputs:
    base: Reference
    patch: Reference
    inventory: Reference
    evidence: tuple[Reference, ...]


@dataclass(frozen=True)
class Prepared:
    candidate: str
    target: str
    height: int
    state_hash: str
    tree: Tree
    checked_objects: tuple[str, ...]


def tree_bytes(tree: Tree) -> bytes:
    records: list[JSON] = [
        {"path": path, "content": content.decode("utf-8")} for path, content in tree.files
    ]
    raw = dumps({"version": "repository-tree/v1", "files": records})
    require(len(raw) <= MAX_ARTIFACT_BYTES, "LIMIT", "encoded tree bundle ceiling")
    return raw


def decode_tree(raw: bytes) -> Tree:
    require(len(raw) <= MAX_ARTIFACT_BYTES, "LIMIT", "encoded tree bundle ceiling")
    value = document(raw)
    fields(value, "version files")
    require(value["version"] == "repository-tree/v1", "VERSION", "tree bundle version")
    records = []
    for item in array(value["files"], limit=MAX_FILES):
        record = obj(item)
        fields(record, "path content")
        content = record["content"]
        require(isinstance(content, str), "SHAPE", "UTF-8 source string required")
        records.append((text(record["path"], limit=240), cast(str, content).encode("utf-8")))
    return Tree(tuple(records))


def contract_digest(contract: Contract) -> str:
    record = cast(Object, asdict(contract))
    record["allowed_paths"] = list(contract.allowed_paths)
    return digest_bytes(dumps(record))


def prepare(
    state: State,
    candidate_id: str,
    wanted: Contract,
    inputs: Inputs,
    store: ArtifactStore,
    *,
    access: Access,
) -> Prepared:
    """Read and check again on every call. No cached acceptance grants future authority."""
    access.authorize(state.mission, "read")
    raw_state = state_bytes(state)
    require(decode(raw_state) == state, "STATE", "invalid committed state structure")
    require(state.mode == "running", "PAUSED", "mission is not preparing new use")
    selected = next((item for item in state.candidates if item.identity == candidate_id), None)
    require(selected is not None, "NOT_FOUND", "candidate missing")
    candidate = cast(Candidate, selected)
    require(
        status(candidate, state.credentials, state.height) == "accepted",
        "ACCEPTANCE",
        "candidate is not accepted",
    )
    target = contract_digest(wanted)
    require(
        candidate.target == target and candidate.artifact == wanted.result_tree,
        "BINDING",
        "requested use differs from accepted contract",
    )
    require(state.height < wanted.deadline_height, "EXPIRED", "contract expired")
    require(wanted.checker_digest == CHECKER_DIGEST, "BINDING", "installed checker differs")
    require(len(inputs.evidence) <= 12, "LIMIT", "evidence reference count")
    refs = {ref.digest: ref for ref in inputs.evidence}
    require(len(refs) == len(inputs.evidence), "DUPLICATE", "duplicate evidence reference")
    require(
        set(refs) == {item.evidence for item in candidate.observations},
        "EVIDENCE",
        "exact retained evidence inventory required",
    )
    checked: set[str] = set()

    def read(ref: Reference, kind: str, ceiling: int) -> bytes:
        require(
            ref.scope == state.mission and ref.manifest == target,
            "SCOPE",
            "artifact reference scope or contract differs",
        )
        require(
            ref.kind == kind and ref.content_type == "application/json",
            "SHAPE",
            "artifact reference purpose differs",
        )
        require(ref.length <= ceiling, "LIMIT", "artifact profile byte ceiling")
        raw = store.get(ref, access=access)
        require(
            len(raw) == ref.length and digest_bytes(raw) == ref.digest,
            "INTEGRITY",
            "artifact bytes differ from reference",
        )
        checked.add(ref.digest)
        return raw

    base = decode_tree(read(inputs.base, "source-tree", MAX_ARTIFACT_BYTES))
    patch = read(inputs.patch, "patch", wanted.max_patch_bytes)
    require(inputs.patch.digest == wanted.patch_digest, "BINDING", "patch reference differs")
    cases = read(inputs.inventory, "evidence", 262144)
    require(digest_bytes(cases) == wanted.test_inventory_digest, "BINDING", "inventory differs")
    request, _ = inventory(cases)
    tree = apply_patch(base, patch, wanted)
    require(request["path"] in dict(tree.files), "NOT_FOUND", "entry module missing")
    reports = {identity: document(read(ref, "evidence", 4096)) for identity, ref in refs.items()}
    tasks = {item.identity: item for item in state.tasks}
    for attestation in candidate.observations:
        report = reports[attestation.evidence]
        fields(
            report,
            "contract_digest height result_tree inventory_digest checker_digest "
            "image stdout_digest stderr_digest case_match reason",
        )
        require(
            report["contract_digest"] == target
            and report["result_tree"] == tree.digest
            and report["inventory_digest"] == wanted.test_inventory_digest
            and report["checker_digest"] == wanted.checker_digest
            and report["image"] == wanted.image,
            "BINDING",
            "verification report target differs",
        )
        work = tasks[attestation.task]
        height = integer(report["height"], low=work.started, high=attestation.height)
        require(
            height < work.until and height <= wanted.deadline_height,
            "EVIDENCE",
            "report outside attempted verification window",
        )
        fingerprint(report["stdout_digest"])
        fingerprint(report["stderr_digest"])
        expected = None if attestation.verdict == "unknown" else attestation.verdict == "pass"
        require(report["case_match"] is expected, "EVIDENCE", "signed verdict contradicts report")
        reason = text(report["reason"], limit=128)
        require(
            expected is None or reason == ("cases_match" if expected else "cases_differ"),
            "EVIDENCE",
            "report reason contradicts verdict",
        )
    return Prepared(
        candidate.identity,
        target,
        state.height,
        digest_bytes(raw_state),
        tree,
        tuple(sorted(checked)),
    )
