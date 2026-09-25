"""Generate the pinned CometBFT ABCI bindings; no runtime code generation."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import grpc_tools

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "src/checkedflow/distributed/proto"


def main() -> None:
    source, output = BASE / "source", BASE / "generated"
    output.mkdir(parents=True, exist_ok=True)
    selected: set[str] = set()

    def visit(name: str) -> None:
        if name in selected or name.startswith("google/"):
            return
        selected.add(name)
        for child in re.findall(r'^import "([^"]+)";', (source / name).read_text(), re.M):
            visit(child)

    visit("tendermint/abci/types.proto")
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{source}",
        f"-I{Path(grpc_tools.__file__).parent / '_proto'}",
        f"--python_out={output}",
        f"--pyi_out={output}",
        f"--grpc_python_out={output}",
        *sorted(selected),
    ]
    subprocess.run(command, check=True, cwd=source)
    for path in output.rglob("*"):
        if path.is_dir():
            (path / "__init__.py").touch()
        elif path.suffix in {".py", ".pyi"}:
            content = path.read_text(encoding="utf-8")
            content = re.sub(
                r"^from (tendermint|gogoproto)([.\w]*) import ",
                r"from checkedflow.distributed.proto.generated.\1\2 import ",
                content,
                flags=re.M,
            )
            path.write_text(content, encoding="utf-8")
    for directory in [BASE.parent, BASE, output]:
        (directory / "__init__.py").touch(exist_ok=True)
    manifest = {
        "cometbft": "0.40.0",
        "gogoproto": "1.7.2",
        "sources": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in sorted(selected)
        },
    }
    (BASE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
