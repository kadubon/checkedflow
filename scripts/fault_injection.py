"""Selected invariant-breaking mutants must be killed by existing regression tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    (
        "quorum",
        "len(ctx.signers & s.organizations.keys()) >= 3",
        "len(ctx.signers & s.organizations.keys()) >= 1",
        "test_administration_cannot_be_self_authorized",
    ),
    (
        "expiry",
        "height >= task.deadline",
        "height > task.deadline",
        "test_expiry_keeps_cost_and_requires_reconciliation",
    ),
    (
        "capacity",
        'mission.verification_reserve if task.phase != "verify" else 0',
        "0",
        "test_capacity_reserves_verification_first",
    ),
    (
        "fence",
        'task.fence == integer(p["fence"], low=1)',
        'integer(p["fence"], low=1) > 0',
        "test_expiry_keeps_cost_and_requires_reconciliation",
    ),
    (
        "dedup",
        "len(identities)",
        "len(live)",
        "test_full_lifecycle_preserves_roles_and_funded_verification",
    ),
]


def main() -> None:
    outcomes = []
    for name, old, new, test in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="checkedflow-mutant-") as temporary:
            directory = Path(temporary)
            shutil.copytree(
                ROOT / "src/checkedflow",
                directory / "checkedflow",
                ignore=shutil.ignore_patterns("__pycache__", "proto"),
            )
            path = directory / "checkedflow/core/machine.py"
            source = path.read_text()
            if old not in source:
                raise SystemExit(f"mutant no longer applies: {name}")
            path.write_text(source.replace(old, new, 1))
            environment = os.environ | {
                "PYTHONPATH": str(directory),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(ROOT / "tests/test_core.py"),
                    "-k",
                    test,
                    "-q",
                    "-p",
                    "no:cacheprovider",
                ],
                env=environment,
                cwd=directory,
                capture_output=True,
                timeout=120,
            )
            killed = result.returncode == 1 and b"AssertionError" in result.stdout
            # pytest.raises failures also kill admission/expiry mutants.
            killed = killed or (result.returncode == 1 and b"DID NOT RAISE" in result.stdout)
            outcomes.append({"mutant": name, "killed": killed, "test": test})
            if not killed:
                print(
                    result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace")
                )
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/fault-injection.json").write_text(json.dumps(outcomes, indent=2))
    print(json.dumps(outcomes))
    if not all(row["killed"] for row in outcomes):
        raise SystemExit("An invariant-breaking mutation survived or failed to run.")


if __name__ == "__main__":
    main()
