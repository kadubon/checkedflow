"""Immutable, bounded repository-patch inputs; never execute candidate Python here.

The initial patch encoding replaces complete UTF-8 files. It deliberately does not invoke
git, unpack archives, resolve URLs, follow links or interpret candidate build configuration.
"""

import re
from dataclasses import dataclass
from hashlib import sha256
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
from checkedflow.wire import document, dumps

DOMAIN = "repository-patch/v1"
MAX_FILES = 128
MAX_FILE_BYTES = 262144
MAX_TREE_BYTES = 2097152
MAX_CASES = 128
HEX = re.compile(r"[0-9a-f]{64}\Z")
COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}\Z")
RESERVED = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
)
PROTECTED = frozenset(
    {"tests", "test", "conftest.py", "setup.py", "sitecustomize.py", "usercustomize.py"}
)


def digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _digest(value: JSON) -> str:
    result = text(value)
    require(HEX.fullmatch(result) is not None, "BINDING", "SHA-256 digest required")
    return result


def normalized_path(value: str) -> str:
    """One portable path spelling; actual extraction must additionally use no-follow I/O."""
    require(0 < len(value) <= 240, "PATH", "path length outside profile")
    parts = value.split("/")
    for part in parts:
        require(
            COMPONENT.fullmatch(part) is not None
            and not part.endswith(".")
            and part.split(".")[0].lower() not in RESERVED,
            "PATH",
            "nonportable path component",
        )
    return value


@dataclass(frozen=True)
class Tree:
    """Sorted immutable regular-file bytes. No permissions, hooks or links are representable."""

    files: tuple[tuple[str, bytes], ...]

    def __post_init__(self) -> None:
        require(0 < len(self.files) <= MAX_FILES, "LIMIT", "file count outside profile")
        seen: set[str] = set()
        total = 0
        previous = ""
        for name, content in self.files:
            normalized_path(name)
            require(previous < name, "PATH", "files must be unique and sorted")
            require(name.lower() not in seen, "PATH", "case-insensitive filename collision")
            require(isinstance(content, bytes), "SHAPE", "file bytes required")
            require(len(content) <= MAX_FILE_BYTES, "LIMIT", "file byte ceiling exceeded")
            require(b"\x00" not in content, "SHAPE", "binary files are outside this profile")
            try:
                content.decode("utf-8")
            except UnicodeError as exc:
                raise Failure("UNICODE", "file must be UTF-8") from exc
            seen.add(name.lower())
            previous = name
            total += len(content)
        require(total <= MAX_TREE_BYTES, "LIMIT", "tree byte ceiling exceeded")
        for name in seen:
            parts = name.split("/")
            require(
                not any("/".join(parts[:i]) in seen for i in range(1, len(parts))),
                "PATH",
                "file/directory path collision",
            )

    @property
    def digest(self) -> str:
        manifest: list[JSON] = [
            {"path": name, "size": len(content), "sha256": digest_bytes(content)}
            for name, content in self.files
        ]
        return digest_bytes(dumps({"version": DOMAIN, "files": manifest}))


@dataclass(frozen=True)
class Contract:
    """Approved immutable verification target. Construction does not establish approval."""

    repository: str
    base_commit: str
    base_tree: str
    result_tree: str
    patch_digest: str
    image: str
    checker_digest: str
    test_inventory_digest: str
    lint_config_digest: str
    receiver: str
    allowed_paths: tuple[str, ...]
    max_changed_files: int
    max_patch_bytes: int
    cpu_seconds: int
    memory_bytes: int
    output_bytes: int
    deadline_height: int
    allow_draft_pr: bool = False

    def __post_init__(self) -> None:
        require(
            re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) is not None,
            "SCOPE",
            "repository owner/name required; URLs are not accepted",
        )
        require(
            re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.base_commit) is not None,
            "BINDING",
            "exact git object identity required",
        )
        for value in (
            self.base_tree,
            self.result_tree,
            self.patch_digest,
            self.checker_digest,
            self.test_inventory_digest,
            self.lint_config_digest,
        ):
            _digest(value)
        require(
            re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", self.image) is not None,
            "BINDING",
            "pinned prebuilt image required",
        )
        text(self.receiver, limit=128)
        require(
            0 < len(self.allowed_paths) <= MAX_FILES
            and tuple(sorted(set(self.allowed_paths))) == self.allowed_paths,
            "PATH",
            "explicit sorted allowed paths required",
        )
        for path in self.allowed_paths:
            normalized_path(path)
            require(
                path.endswith(".py")
                and not any(part.lower() in PROTECTED for part in path.split("/"))
                and not path.split("/")[-1].lower().startswith("test_"),
                "SCOPE",
                "protected or non-Python patch path",
            )
        integer(self.max_changed_files, low=1, high=MAX_FILES)
        integer(self.max_patch_bytes, low=1, high=MAX_TREE_BYTES)
        integer(self.cpu_seconds, low=1, high=60)
        integer(self.memory_bytes, low=16777216, high=1073741824)
        integer(self.output_bytes, low=1, high=MAX_FILE_BYTES)
        integer(self.deadline_height, low=1)
        require(type(self.allow_draft_pr) is bool, "SHAPE", "boolean effect permission required")


def apply_patch(base: Tree, raw: bytes, contract: Contract) -> Tree:
    """Validate and apply file replacements in memory; do not execute or write host files."""
    require(base.digest == contract.base_tree, "BINDING", "base tree differs from contract")
    require(len(raw) <= contract.max_patch_bytes, "LIMIT", "patch byte ceiling exceeded")
    require(digest_bytes(raw) == contract.patch_digest, "BINDING", "patch bytes differ")
    patch = document(raw)
    fields(patch, "version changes")
    require(patch["version"] == DOMAIN, "VERSION", "unsupported patch format")
    changes = array(patch["changes"], limit=contract.max_changed_files)
    require(bool(changes), "SHAPE", "empty patch")
    result = dict(base.files)
    seen: set[str] = set()
    for value in changes:
        change = obj(value)
        fields(change, "path before content")
        name = normalized_path(text(change["path"]))
        require(name in contract.allowed_paths, "SCOPE", "patch path not approved")
        require(name not in seen, "PATH", "duplicate patch target")
        seen.add(name)
        previous = result.get(name)
        require(
            change["before"] == (digest_bytes(previous) if previous is not None else None),
            "BINDING",
            "patch preimage differs",
        )
        content = change["content"]
        if content is None:
            require(previous is not None, "NOT_FOUND", "cannot delete missing path")
            del result[name]
        else:
            require(isinstance(content, str), "SHAPE", "UTF-8 source string required")
            result[name] = cast(str, content).encode("utf-8")
    tree = Tree(tuple(sorted(result.items())))
    require(tree.digest == contract.result_tree, "BINDING", "result tree differs from contract")
    return tree


def check_outputs(raw: bytes, expected: tuple[Object, ...], *, byte_limit: int) -> bool:
    """Compare strict bounded records against evaluator-owned cases outside candidate process.

    Each expected record binds `case` and `output`. The candidate can inspect inputs; these
    are not secret tests. A result only covers this inventory, never general equivalence.
    """
    integer(byte_limit, low=1, high=MAX_FILE_BYTES)
    require(0 < len(expected) <= MAX_CASES, "EVIDENCE", "nonempty test inventory required")
    require(len(raw) <= byte_limit, "LIMIT", "candidate output ceiling exceeded")
    case_ids: set[str] = set()
    for record in expected:
        fields(record, "case output")
        case = text(record["case"], limit=128)
        require(case not in case_ids, "EVIDENCE", "duplicate expected case")
        case_ids.add(case)
        dumps(record)
    report = document(raw)
    fields(report, "results")
    actual = array(report["results"], limit=MAX_CASES)
    require(len(actual) == len(expected), "EVIDENCE", "missing or unexpected results")
    for value, wanted in zip(actual, expected, strict=True):
        record = obj(value)
        fields(record, "case output")
        require(record["case"] == wanted["case"], "EVIDENCE", "test inventory/order differs")
    # Canonical bytes distinguish booleans from integers, unlike Python's == operator.
    return dumps(actual) == dumps(list(expected))
