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
                "--cov-branch",
                "--cov-report=json:reports/coverage.json",
                "--junitxml=reports/unit.xml",
                "-q",
            ]
        )
        totals = json.loads((ROOT / "reports/coverage.json").read_text())["totals"]
        for key in ("percent_statements_covered", "percent_branches_covered"):
            if totals[key] < 95:
                raise SystemExit(f"Core {key} below 95%: {totals[key]}")
        run([sys.executable, "scripts/fault_injection.py"])


if __name__ == "__main__":
    main()
