"""Evaluate packaged monitoring rules with the real pinned promtool, without listeners."""

import argparse
import hashlib
import json
import subprocess
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

from fetch_promtool import BINARY_HASH

NAMES = ("alerts.yml", "alert-tests.yml", "prometheus.yml.example", "dashboard.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promtool", type=Path, required=True)
    args = parser.parse_args()
    binary = args.promtool.resolve()
    with binary.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != BINARY_HASH:
            raise SystemExit("Unqualified promtool binary")
    with TemporaryDirectory(prefix="checkedflow-monitoring-") as temporary:
        root = Path(temporary)
        for name in NAMES:
            (root / name).write_bytes(
                files("checkedflow").joinpath("data", "monitoring", name).read_bytes()
            )
        for command in (
            ["check", "rules", "alerts.yml"],
            ["test", "rules", "alert-tests.yml"],
            ["check", "config", "--syntax-only", "prometheus.yml.example"],
        ):
            subprocess.run([str(binary), *command], cwd=root, check=True, timeout=30)
        dashboard = json.loads((root / "dashboard.json").read_text())
        # Query and layout checks do not claim a rendered Grafana deployment.
        ids = [panel["id"] for panel in dashboard["panels"]]
        if len(ids) != 8 or len(set(ids)) != 8:
            raise SystemExit("Expected eight unique dashboard panels")
        for panel in dashboard["panels"]:
            if panel["fieldConfig"]["defaults"]["custom"]["spanNulls"] is not False:
                raise SystemExit("Dashboard must not hide missing observations")
            subprocess.run(
                [str(binary), "--experimental", "promql", "format", panel["targets"][0]["expr"]],
                cwd=root,
                check=True,
                timeout=10,
                stdout=subprocess.DEVNULL,
            )
    print("Monitoring rules and eight dashboard queries checked; no deployment qualification")


if __name__ == "__main__":
    main()
