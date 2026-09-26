"""gVisor-only OCI execution. No fallback to an unisolated subprocess."""

import platform
import shutil
import subprocess  # nosec B404
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from checkedflow.core.values import JSON, Failure, Object, array, obj, require
from checkedflow.domains.repository_patch import Tree
from checkedflow.tree_workspace import materialize
from checkedflow.wire import document, dumps, loads


@dataclass(frozen=True)
class Limits:
    seconds: int = 15
    memory_bytes: int = 536870912
    processes: int = 128
    output_bytes: int = 262144


@dataclass(frozen=True)
class Result:
    status: str
    returncode: int
    stdout: bytes
    stderr: bytes
    reason: str


def command(
    image: str, work: Path, name: str, argv: tuple[str, ...], limits: Limits, docker: str = "docker"
) -> list[str]:
    """Construct a closed Docker+runsc launch; caller configuration supplies argv and image."""
    require(
        len(image) > 72
        and image[-72:-64] == "@sha256:"
        and all(c in "0123456789abcdef" for c in image[-64:]),
        "IMAGE",
        "immutable image required",
    )
    require(
        1 <= limits.seconds <= 3600
        and 1 <= limits.processes <= 1024
        and 16777216 <= limits.memory_bytes <= 8589934592
        and 1 <= limits.output_bytes <= 1048576,
        "LIMIT",
        "invalid sandbox limits",
    )
    require(
        bool(argv) and all(isinstance(a, str) and a and "\x00" not in a for a in argv),
        "ARGV",
        "invalid argument vector",
    )
    return [
        docker,
        "run",
        "--name",
        name,
        "--runtime=runsc",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--user=65534:65534",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--memory={limits.memory_bytes}",
        f"--memory-swap={limits.memory_bytes}",
        "--cpus=1",
        f"--pids-limit={limits.processes}",
        "--ulimit=nofile=64:64",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=16777216",
        "--workdir=/work",
        "-i",
        "--mount",
        f"type=bind,src={work.resolve()},dst=/work,readonly",
        "--entrypoint",
        argv[0],
        image,
        *argv[1:],
    ]


class GVisorRunner:
    """Run a fixed image through a host-operated Docker daemon configured with runsc.

    The daemon and host are part of this operator's trusted execution base. The submitted
    program receives neither the daemon socket nor credentials nor a writable host mount.
    """

    def __init__(self, image: str, *, limits: Limits | None = None, docker: str = "docker") -> None:
        self.image, self.limits = image, limits or Limits()
        executable = shutil.which(docker)
        require(executable is not None, "SANDBOX_UNAVAILABLE", "Docker executable missing")
        self.docker = cast(str, executable)

    def check(self) -> Object:
        require(platform.system() == "Linux", "SANDBOX_UNAVAILABLE", "Linux execution required")
        try:
            result = subprocess.run(  # nosec B603
                [self.docker, "info", "--format", "{{json .Runtimes}}"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Failure("SANDBOX_UNAVAILABLE", "Docker daemon probe failed") from exc
        require(result.returncode == 0, "SANDBOX_UNAVAILABLE", "Docker daemon unavailable")
        runtimes = obj(loads(result.stdout))
        require("runsc" in runtimes, "SANDBOX_UNAVAILABLE", "runsc runtime missing")
        return {"linux": True, "runtime": "runsc", "image": self.image}

    def run(self, argv: tuple[str, ...], files: dict[str, str], stdin: JSON) -> Result:
        input_bytes = dumps(stdin) + b"\n"
        self.check()
        require(len(files) <= 32, "LIMIT", "file limit")
        require(
            sum(len(v.encode("utf-8")) for v in files.values()) <= 524288,
            "LIMIT",
            "file byte limit",
        )
        with tempfile.TemporaryDirectory(prefix="checkedflow-") as temporary:
            work = Path(temporary)
            work.chmod(0o755)
            for filename, content in files.items():
                require(
                    filename not in {"", ".", ".."}
                    and Path(filename).name == filename
                    and "/" not in filename
                    and "\\" not in filename
                    and ":" not in filename,
                    "PATH",
                    "files must be plain basenames",
                )
                target = work / filename
                target.write_text(content, encoding="utf-8")
                target.chmod(0o444)
            return self._execute(argv, work, input_bytes)

    def run_tree(self, argv: tuple[str, ...], tree: Tree, stdin: JSON) -> Result:
        """Execute bounded repository bytes under the same closed isolation profile.

        Callers must separately authenticate the contract and compare external evidence.
        A successful process exit is not verification or authorization.
        """
        input_bytes = dumps(stdin) + b"\n"
        self.check()
        with tempfile.TemporaryDirectory(prefix="checkedflow-tree-") as temporary:
            work = materialize(Path(temporary), tree)
            return self._execute(argv, work, input_bytes)

    def _execute(self, argv: tuple[str, ...], work: Path, input_bytes: bytes) -> Result:
        name = "checkedflow-" + uuid.uuid4().hex
        args = command(self.image, work, name, argv, self.limits, self.docker)
        process = subprocess.Popen(  # nosec B603
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = bytearray(), bytearray()
        overflow = threading.Event()

        def drain(stream: BinaryIO, output: bytearray) -> None:
            # A separate thread per pipe prevents a child from blocking on stderr.
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                remaining = self.limits.output_bytes - len(output)
                output.extend(chunk[: max(remaining, 0)])
                if len(chunk) > remaining:
                    overflow.set()

        threads = [
            threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
        ]

        def feed() -> None:
            stream = cast(BinaryIO, process.stdin)
            try:
                stream.write(input_bytes)
                stream.close()
            except (BrokenPipeError, OSError):
                pass

        threads.append(threading.Thread(target=feed, daemon=True))
        for thread in threads:
            thread.start()
        reason = "completed"
        try:
            deadline = time.monotonic() + self.limits.seconds
            while process.poll() is None:
                if overflow.is_set() or time.monotonic() >= deadline:
                    reason = "output_limit" if overflow.is_set() else "timeout"
                    break
                time.sleep(0.02)
        finally:
            try:
                cleanup = subprocess.run(  # nosec B603
                    [self.docker, "rm", "--force", name],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                cleanup_known = cleanup.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                cleanup_known = False
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
            if not cleanup_known:
                reason = "cleanup_unknown"
        if overflow.is_set() and reason == "completed":
            reason = "output_limit"
        if process.returncode != 0 and reason == "completed":
            reason = f"exit_nonzero:{process.returncode}"
        status = "reported" if reason == "completed" and process.returncode == 0 else "unknown"
        return Result(status, process.returncode, bytes(stdout), bytes(stderr), reason)

    def python(self, source: str, inputs: list[JSON]) -> list[JSON]:
        # Expected outputs are compared outside the sandbox. Candidate code cannot edit the checker.
        wrapper = (
            "import json, sys\n"
            "from candidate import solve\n"
            "inputs = json.load(sys.stdin)\n"
            "outputs = [solve(value) for value in inputs]\n"
            "sys.stdout.write(json.dumps(outputs, separators=(',', ':'), allow_nan=False))\n"
        )
        result = self.run(
            ("python", "-B", "-s", "/work/check.py"),
            {"candidate.py": source, "check.py": wrapper},
            inputs,
        )
        require(result.status == "reported", "SANDBOX_RESULT", result.reason)
        return array(loads(result.stdout), limit=4096)

    def generate(self, argv: tuple[str, ...], request: Object) -> Object:
        result = self.run(argv, {}, request)
        require(result.status == "reported", "GENERATOR", result.reason)
        return document(result.stdout)
