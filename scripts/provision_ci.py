"""Provision pinned Linux qualification tools on a disposable GitHub-hosted runner only."""

import hashlib
import json
import os
import subprocess
import tarfile
import urllib.request
from pathlib import Path


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def download(url: str, path: Path, algorithm: str, expected: str) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, path.open("wb") as output:
        while chunk := response.read(1048576):
            output.write(chunk)
    if hashlib.new(algorithm, path.read_bytes()).hexdigest() != expected:
        raise SystemExit("Provisioned download digest mismatch")


def main() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise SystemExit("This provisioning script requires a disposable Linux Actions runner")
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "deploy/runtime-lock.json").read_text())
    tools = Path(os.environ["RUNNER_TEMP"]) / "checkedflow-tools"
    tools.mkdir(exist_ok=True)
    gvisor = lock["gvisor"]
    archive = tools / "gvisor.tar.bz2"
    download(gvisor["source_url"], archive, "sha512", gvisor["archive_sha512"])
    with tarfile.open(archive) as bundle:
        bundle.extractall(tools, filter="data")
    candidates = [p for p in tools.rglob("runsc") if p.is_file()]
    if len(candidates) != 1:
        raise SystemExit("Unexpected gVisor archive layout")
    runtime = candidates[0]
    runtime.chmod(0o755)
    # Keep runsc adjacent to any archive-provided binaries rather than copying one executable.
    run("sudo", str(runtime), "install")
    run("sudo", "systemctl", "restart", "docker")
    comet = lock["cometbft"]
    download(comet["source_url"], tools / "cometbft.zip", "sha256", comet["source_sha256"])
    run("go", "install", comet["module"] + "/cmd/cometbft@" + comet["version"])
    executable = Path.home() / "go/bin/cometbft"
    # Go's checksum database additionally authenticates the dependency graph.
    module = subprocess.check_output(["go", "version", "-m", str(executable)], text=True)
    if comet["module_sum"] not in module:
        raise SystemExit("CometBFT module identity mismatch")
    run("docker", "pull", lock["python_image"])
    with Path(os.environ["GITHUB_ENV"]).open("a") as output:
        output.write(f"CHECKEDFLOW_IMAGE={lock['python_image']}\n")
        output.write(f"CHECKEDFLOW_COMETBFT={executable}\n")
    run(
        "docker",
        "run",
        "--rm",
        "--runtime=runsc",
        "--network=none",
        lock["python_image"],
        "python",
        "-c",
        "print('gVisor qualification preflight')",
    )


if __name__ == "__main__":
    main()
