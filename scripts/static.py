"""Offline architecture, determinism, schema, research, documentation and package contracts."""

import ast
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote

from generate_contracts import artifacts
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/checkedflow"


def anchors(content: str) -> set[str]:
    """GitHub-style anchors for the plain Markdown headings used by this repository."""
    content = re.sub(r"^```.*?^```[^\n]*", "", content, flags=re.M | re.S)
    seen: dict[str, int] = {}
    result = set()
    for heading in re.findall(r"^#{1,6} (.+)$", content, re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.lower().replace("`", "")).replace(" ", "-")
        suffix = seen.get(slug, 0)
        result.add(slug + (f"-{suffix}" if suffix else ""))
        seen[slug] = suffix + 1
    return result


def implementation_ledger() -> list[str]:
    """Check traceability, not evidence authenticity or release qualification."""
    errors = []
    ledger = json.loads((ROOT / "docs/implementation-0.2.0.json").read_text(encoding="utf-8"))
    specification = (ROOT / "docs/specification-0.2.0.md").read_text(encoding="utf-8")
    if hashlib.sha256(specification.encode("utf-8")).hexdigest() != ledger["specification_sha256"]:
        errors.append("implementation ledger specification hash differs")
    lines = specification.splitlines()
    requirements = {row["id"]: row for row in ledger["requirements"]}
    components = {row["id"]: row for row in ledger["components"]}
    if len(requirements) != len(ledger["requirements"]) or len(components) != len(
        ledger["components"]
    ):
        errors.append("duplicate implementation ledger identity")
    expected = {identity: set() for identity in requirements}
    for identity, row in components.items():
        for requirement in row["requirements"]:
            if requirement not in expected:
                errors.append(f"unknown requirement in component: {identity}: {requirement}")
            else:
                expected[requirement].add("component:" + identity)
        for group in ("implementation", "tests", "documentation", "evidence"):
            for filename in row.get(group, []):
                path = (ROOT / filename).resolve()
                if not path.is_relative_to(ROOT) or not path.is_file():
                    errors.append(f"missing or external component path: {identity}: {filename}")
    for identity, row in requirements.items():
        line = row["source_line"]
        if type(line) is not int or not 1 <= line <= len(lines) or lines[line - 1] != row["text"]:
            errors.append(f"requirement differs from frozen specification: {identity}")
        if row["status"] not in ledger["statuses"]:
            errors.append(f"unknown requirement status: {identity}")
        linked = {entry for entry in row["evidence"] if entry.startswith("component:")}
        if linked != expected[identity] or (linked and row["status"] == "NOT_STARTED"):
            errors.append(f"inconsistent requirement/component links: {identity}")
    if ledger["not_release_authority"] is not True:
        errors.append("implementation ledger cannot be release authority")
    return errors


def inspect() -> list[str]:
    errors = implementation_ledger()
    allowed = {"copy", "dataclasses", "hashlib", "typing", "json", "collections.abc"}
    for path in PACKAGE.rglob("*.py"):
        if "proto" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(n.name for n in node.names)
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            if (
                "core" in path.parts
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id
                in {
                    "open",
                    "eval",
                    "exec",
                    "compile",
                    "input",
                    "print",
                    "id",
                    "hash",
                }
            ):
                errors.append(f"nondeterministic or effectful core call: {path}:{node.lineno}")
        if "core" in path.parts:
            for name in imports:
                if not (name.startswith("checkedflow.core.") or name in allowed):
                    errors.append(f"forbidden core dependency: {path}: {name}")
        if path.name == "artifact_io.py":
            for name in imports:
                if not (
                    name.startswith("checkedflow.core.")
                    or name in {"dataclasses", "hashlib", "typing"}
                ):
                    errors.append(f"provider dependency in artifact interface: {path}: {name}")
        if path.name in {
            "wire.py",
            "contracts.py",
            "identity.py",
            "serialization.py",
            "runtime.py",
            "operational_identity.py",
            "operational_runtime.py",
            "operational_codec.py",
        }:
            for name in imports:
                if name.startswith(
                    (
                        "checkedflow.distributed",
                        "checkedflow.worker",
                        "checkedflow.runner",
                        "checkedflow.agents",
                        "a2a",
                        "mcp",
                    )
                ):
                    errors.append(f"inverted dependency: {path}: {name}")
    for name, expected in artifacts().items():
        actual = json.loads((PACKAGE / "data" / name).read_text(encoding="utf-8"))
        if actual != expected:
            errors.append(f"stale normative contract: {name}")
    for path in (PACKAGE / "data").glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        if re.search(r'"\$ref"\s*:\s*"(?!#)', path.read_text()):
            errors.append(f"network schema reference: {path}")
    state_schema = json.loads((PACKAGE / "data/operational-state.schema.json").read_text())
    configuration_schema = json.loads(
        (PACKAGE / "data/operational-configuration.schema.json").read_text()
    )
    if configuration_schema["properties"]["state"] != state_schema:
        errors.append("embedded operational state schema differs from the standalone contract")
    proto = PACKAGE / "distributed/proto"
    manifest = json.loads((proto / "manifest.json").read_text())
    actual_sources = {
        path.relative_to(proto / "source").as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (proto / "source").rglob("*.proto")
    }
    if manifest["cometbft"] != "0.40.0" or manifest["sources"] != actual_sources:
        errors.append("CometBFT protocol source manifest differs from bundled bytes")
    research = json.loads((PACKAGE / "data/research.json").read_text())
    for kind, count in (("paper", 19), ("software", 18)):
        if sum(row["kind"] == kind for row in research["resources"]) != count:
            errors.append(f"research count differs: {kind}")
    for row in research["resources"]:
        if not row["observed_content"] or not row["not_implemented_or_claimed"]:
            errors.append(f"incomplete provenance: {row['id']}")
        for link in [row["implementation"]["file"], *row["verification"]]:
            if not (ROOT / link).is_file():
                errors.append(f"missing research implementation/test: {link}")
    for path in [
        ROOT / "README.md",
        ROOT / "skills.md",
        *(ROOT / "docs").glob("*.md"),
        *(ROOT / ".agents").rglob("*.md"),
    ]:
        content = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^ )]+)\)", content):
            if target.startswith(("http:", "https:", "mailto:")):
                continue
            name, _, fragment = unquote(target).partition("#")
            destination = (path.parent / name).resolve() if name else path
            if not destination.exists():
                errors.append(f"broken local document link: {path.name}: {target}")
            elif (
                fragment
                and destination.suffix == ".md"
                and fragment not in anchors(destination.read_text(encoding="utf-8"))
            ):
                errors.append(f"broken local heading link: {path.name}: {target}")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    if project["project"]["name"] != "checkedflow" or project["project"]["license"] != "Apache-2.0":
        errors.append("package identity/license mismatch")
    return errors


if __name__ == "__main__":
    failures = inspect()
    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"Offline static contracts: {len(failures)} failures")
    raise SystemExit(bool(failures))
