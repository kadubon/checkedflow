"""Qualify a pinned external service against an isolated installation of the supplied wheel."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, environment: dict[str, str], timeout: int, log: Path) -> None:
    with log.open("ab") as output:
        process = subprocess.Popen(
            command,
            env=environment,
            cwd=log.parent,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            if os.name == "nt"
            else 0,
        )
        try:
            result = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
            raise SystemExit(
                "Service qualification process exceeded its declared time limit"
            ) from None
        if result:
            raise SystemExit(f"Service qualification stage failed with exit status {result}")


PROFILES = {
    "signer": {
        "extra": "",
        "env": "SIGNER",
        "binary": "VAULT",
        "test": "test_vault_service.py",
        "marker": "signer",
        "case": "test_real_vault_version_binding_nonexport_policy_rotation_and_outage",
        "scope": "real-vault-transit-installed-component",
    },
    "s3": {
        "extra": "[s3]",
        "env": "S3",
        "binary": "S3",
        "test": "test_s3_service.py",
        "marker": "object_store",
        "case": "test_real_s3_tls_conditional_publication_corruption_and_outage",
        "scope": "real-seaweedfs-s3-installed-component",
    },
}


def qualify(provider: str) -> None:
    profile = PROFILES[provider]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--reports", type=Path, default=ROOT / "reports")
    args = parser.parse_args()
    wheels = list(args.dist.resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Exactly one built wheel is required")
    reports = args.reports.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    log, junit = reports / f"{provider}-installed.log", reports / f"{provider}-installed.xml"
    result_file = reports / f"{provider}-installed.json"
    for prior in (log, junit, result_file):
        prior.unlink(missing_ok=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment.update(
        {
            f"CHECKEDFLOW_REQUIRE_{profile['env']}": "1",
            f"CHECKEDFLOW_REQUIRE_INSTALLED_{profile['env']}": "1",
            f"CHECKEDFLOW_{profile['binary']}_BINARY": str(args.binary.resolve()),
        }
    )
    with tempfile.TemporaryDirectory(prefix=f"checkedflow-{provider}-installed-") as folder:
        target = Path(folder) / "env"
        python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run(
            ["uv", "venv", "--python", sys.executable, str(target)],
            environment=environment,
            timeout=60,
            log=log,
        )
        run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                str(wheels[0]) + profile["extra"],
                "pytest==" + importlib.metadata.version("pytest"),
            ],
            environment=environment,
            timeout=120,
            log=log,
        )
        run(
            [
                str(python),
                "-I",
                "-c",
                "import checkedflow, pathlib, sys; "
                "p = pathlib.Path(checkedflow.__file__).resolve().relative_to("
                "pathlib.Path(sys.prefix).resolve()); "
                "assert 'site-packages' in p.parts; "
                "print('Installed import origin:', p.as_posix())",
            ],
            environment=environment,
            timeout=10,
            log=log,
        )
        run(
            [
                str(python),
                "-I",
                "-m",
                "pytest",
                str(ROOT / "tests" / profile["test"]),
                "-m",
                profile["marker"],
                "-q",
                "--junitxml=" + str(junit),
            ],
            environment=environment,
            timeout=180,
            log=log,
        )
    cases = list(ET.parse(junit).iter("testcase"))
    if len(cases) != 1 or cases[0].attrib.get("name") != profile["case"] or list(cases[0]):
        raise SystemExit("Real service case missing, skipped or failed")
    with wheels[0].open("rb") as source:
        wheel_hash = hashlib.file_digest(source, "sha256").hexdigest()
    provenance = json.loads((args.binary.resolve().parent / "provenance.json").read_text())
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    record = {
        "scope": profile["scope"],
        "result": "PASS",
        "wheel": wheels[0].name,
        "wheel_sha256": wheel_hash,
        "python": sys.version.split()[0],
        "pytest": importlib.metadata.version("pytest"),
        "harness_revision": revision,
        "harness_tree_clean": not bool(dirty),
        "provider": provenance,
        "tests": [cases[0].attrib["name"]],
        "release_authority": False,
    }
    result_file.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))
