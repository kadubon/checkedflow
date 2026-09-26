"""Run reproducible offline/static checks and unit tests; infrastructure is a separate gate."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(arguments: list[str]) -> None:
    print("+ " + " ".join(arguments), flush=True)
    subprocess.run(arguments, cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--actionlint", help="absolute actionlint executable path")
    args = parser.parse_args()
    for command in [
        ["ruff", "check", "src", "tests", "scripts"],
        ["ruff", "format", "--check", "src", "tests", "scripts"],
        ["mypy", "src/checkedflow"],
        ["bandit", "-q", "-r", "src/checkedflow", "-c", "pyproject.toml"],
        [sys.executable, "scripts/static.py"],
        [sys.executable, "scripts/security_audit.py"],
    ]:
        run(command)
    actionlint = args.actionlint or shutil.which("actionlint")
    if not actionlint:
        raise SystemExit("Required static check unavailable: actionlint. Supply --actionlint PATH.")
    run(
        [
            actionlint,
            "-config-file",
            ".github/actionlint.yaml",
            "-shellcheck=",
            ".github/workflows/workflow.yml",
        ]
    )
    if not args.static_only:
        run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-m",
                "not integration and not sandbox",
                "--cov=checkedflow.core",
                "--cov=checkedflow.operational_identity",
                "--cov=checkedflow.operational_runtime",
                "--cov=checkedflow.operational_codec",
                "--cov=checkedflow.operational_storage",
                "--cov=checkedflow.domains.repository_patch",
                "--cov-branch",
                "--cov-report=json:reports/coverage.json",
                "--junitxml=reports/unit.xml",
                "-q",
            ]
        )
        coverage = json.loads((ROOT / "reports/coverage.json").read_text())
        core = {
            key: 0
            for key in ("covered_lines", "num_statements", "covered_branches", "num_branches")
        }
        boundaries = {
            "operational_identity.py",
            "operational_runtime.py",
            "operational_codec.py",
            "operational_storage.py",
            "domains/repository_patch.py",
            "core/authority.py",
            "core/request_journal.py",
            "core/operational.py",
        }
        found = set()
        for name, report in coverage["files"].items():
            normalized = name.replace("\\", "/")
            if "/checkedflow/core/" in normalized and normalized.rsplit("/", 1)[-1] in {
                "__init__.py",
                "model.py",
                "machine.py",
                "values.py",
            }:
                for key in core:
                    core[key] += report["summary"][key]
            for boundary in boundaries:
                if normalized.endswith("/" + boundary):
                    found.add(boundary)
                    for key in ("percent_statements_covered", "percent_branches_covered"):
                        if report["summary"][key] < 95:
                            raise SystemExit(f"Authoritative boundary {name} {key} below 95%")
        if found != boundaries or not core["num_statements"] or not core["num_branches"]:
            raise SystemExit("Required authoritative coverage is missing")
        for covered, total in (
            ("covered_lines", "num_statements"),
            ("covered_branches", "num_branches"),
        ):
            if 100 * core[covered] < 95 * core[total]:
                raise SystemExit(f"Legacy core {covered}/{total} below 95%")
        run([sys.executable, "scripts/fault_injection.py"])


if __name__ == "__main__":
    main()
