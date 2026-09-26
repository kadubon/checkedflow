"""Check wheel/sdist contents and installed behavior outside the source checkout."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "checkedflow/py.typed",
    "checkedflow/data/envelope.schema.json",
    "checkedflow/data/state.schema.json",
    "checkedflow/data/generator.schema.json",
    "checkedflow/data/block.schema.json",
    "checkedflow/data/agent-request.schema.json",
    "checkedflow/data/agent-vectors.json",
    "checkedflow/data/agents.json",
    "checkedflow/data/commands.json",
    "checkedflow/data/vectors.json",
    "checkedflow/data/legacy-v1.json",
    "checkedflow/data/operational-envelope.schema.json",
    "checkedflow/data/operational-state.schema.json",
    "checkedflow/data/request-archive.schema.json",
    "checkedflow/data/artifact-reference.schema.json",
    "checkedflow/data/key-command.schema.json",
    "checkedflow/data/repository-patch.schema.json",
    "checkedflow/data/research.json",
    "checkedflow/data/examples/sdk.py",
    "checkedflow/data/examples/agents.py",
    "checkedflow/distributed/proto/LICENSE-CometBFT",
    "checkedflow/distributed/proto/LICENSE-gogoproto",
    "checkedflow/distributed/proto/NOTICE-CometBFT",
]
SMOKE = """
import importlib.resources as r, json, runpy, sys
import checkedflow
from pathlib import Path
origin = Path(checkedflow.__file__).resolve().relative_to(Path(sys.prefix).resolve())
assert "site-packages" in origin.parts
print("Installed import origin:", origin.as_posix())
from checkedflow.runtime import Runtime
from checkedflow.serialization import decode
from checkedflow.wire import dumps, loads
from checkedflow.cli import main
assert checkedflow.__version__ == "0.1.0"
v = json.loads(r.files("checkedflow").joinpath("data/vectors.json").read_text())
for row in v["canonical"]:
    assert dumps(loads(row["input"])).hex() == row["canonical_hex"]
runtime = Runtime(decode(v["lifecycle"]["genesis"]))
for event in v["lifecycle"]["events"]:
    runtime.apply(event["envelope"], height=event["height"])
assert runtime.state_hash == v["lifecycle"]["final_hash"]
suffix = v["lifecycle"]["empty_block_suffix"]
runtime.tick(suffix["height"])
assert runtime.state_hash == suffix["final_hash"]
from checkedflow.recovery import replay_blocks
legacy = json.loads(r.files("checkedflow").joinpath("data/legacy-v1.json").read_text())
assert replay_blocks(decode(legacy["initial"]), legacy["blocks"]).state_hash == (
    "9c12eea3393f018bbac48ad1660b1077c9e8fb6d1b6665eede9ca50cf1817950"
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from checkedflow.operational_identity import Credential, authenticate, sign_command
keys = {(name, 1): Ed25519PrivateKey.generate() for name in ("a", "b", "c", "d")}
registry = {
    (name, revision): Credential(
        name, revision, name, "administrator", "", key.public_key().public_bytes_raw().hex(), 0
    )
    for (name, revision), key in keys.items()
}
command = {
    "api_version": "checkedflow/v2", "chain": "installed-auth-only", "epoch": 0,
    "id": "auth-check", "actor": "a", "revision": 1, "nonce": 1,
    "kind": "mission.pause", "payload": {"mission": "m"}
}
raw = sign_command(command, {identity: key for identity, key in keys.items() if identity[0] != "d"})
parsed, verified = authenticate(
    raw, chain="installed-auth-only", epoch=0, height=0,
    organizations=frozenset("abcd"), registry=registry
)
assert parsed == command
verified.require_administration()
from checkedflow.core.operational import genesis as operational_genesis
from checkedflow.operational_runtime import Runtime as OperationalRuntime
control = OperationalRuntime(operational_genesis(
    "installed-auth-only", "m", tuple("abcd"), tuple(registry.values())
))
command["id"] = "0:pause"
control.apply(sign_command(command, keys), height=1)
assert control.state.mode == "paused"
command.update(id="0:checkpoint", nonce=2, kind="journal.rollover")
archive = control.apply(sign_command(command, keys), height=2)
assert archive.root == control.state.journal.archive_root
assert control.state.journal.epoch == 1
import tempfile
from pathlib import Path
from checkedflow.operational_storage import Store
initial_control = operational_genesis(
    "installed-auth-only", "m", tuple("abcd"), tuple(registry.values())
)
with tempfile.TemporaryDirectory() as folder:
    store = Store(Path(folder) / "control.sqlite", initial_control)
    command.update(id="0:stored", nonce=1)
    committed = store.commit_block(
        1, [sign_command(command, keys)],
        previous_hash=OperationalRuntime(initial_control).state_hash
    )
    assert store.verify_history(expected_hash=committed.state_hash) == store.load()
    from checkedflow.operational_identity import prove_possession
    replacement = Ed25519PrivateKey.generate()
    proposal = command | {
        "epoch": 1, "id": "1:rotation", "nonce": 2, "kind": "key.schedule",
        "payload": {"mission": "m", "identity": "a", "revision": 2,
                    "public_key": replacement.public_key().public_bytes_raw().hex(),
                    "activation_height": 3, "proof": ""}
    }
    proposal = prove_possession(proposal, replacement)
    committed = store.commit_block(
        2, [sign_command(proposal, keys)], previous_hash=committed.state_hash
    )
    new_signers = {pair: signer for pair, signer in keys.items() if pair[0] != "a"}
    new_signers[("a", 2)] = replacement
    after = command | {"epoch": 1, "id": "1:after", "revision": 2,
                       "nonce": 3, "kind": "mission.pause"}
    committed = store.commit_block(
        3, [sign_command(after, new_signers)], previous_hash=committed.state_hash
    )
    assert committed.outcomes == ("OK",)
    assert store.verify_history(expected_hash=committed.state_hash) == store.load()
from io import BytesIO
from hashlib import sha256
from checkedflow.artifacts import Access, LocalStore
from checkedflow.core.artifact import Reference
with tempfile.TemporaryDirectory() as folder:
    artifacts = LocalStore(Path(folder) / "artifacts.sqlite")
    access = Access("installed-check", frozenset({"demo"}), frozenset({"read", "write"}))
    ref = Reference("sha256", sha256(b"installed").hexdigest(), 9,
                    "text/plain", "evidence", "demo", "1" * 64)
    artifacts.put(ref, BytesIO(b"installed"), access=access)
    assert artifacts.get(ref, access=access) == b"installed"
runpy.run_path(str(r.files("checkedflow").joinpath("data/examples/sdk.py")), run_name="__main__")
assert main(["example"]) == 0
from checkedflow.agents.gateway import profile
assert profile()["command_protocol"] == "checkedflow/v1"
assert main(["schema", "agent-request"]) == 0
print("Installed package smoke passed on", sys.version.split()[0])
"""
AGENT_SMOKE = """
import asyncio, sys
import httpx
from a2a.client import A2ACardResolver
from mcp import Client
from checkedflow.agents.a2a import create_app
from checkedflow.agents.gateway import Gateway
from checkedflow.agents.mcp import create_server
class NoNode:
    def state(self):
        raise RuntimeError("discovery must not require ledger authority")
    def submit(self, envelope):
        raise RuntimeError("discovery must never submit work")
async def main():
    gateway = Gateway(NoNode(), "discovery-only", "example")
    async with Client(create_server(gateway)) as client:
        assert len((await client.list_tools()).tools) == 2
        assert len((await client.list_resources()).resources) == 3
        assert len((await client.list_resource_templates()).resource_templates) == 3
        assert (await client.list_prompts()).prompts[0].name == "checkedflow_review"
    app = create_app(gateway, "http://127.0.0.1/rpc", "test-only-installation-token-00000000")
    async with (
        app.app.router.lifespan_context(app.app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app)) as transport,
    ):
        card = await A2ACardResolver(transport, "http://127.0.0.1").get_agent_card()
        assert card.supported_interfaces[0].protocol_version == "1.0"
        assert card.capabilities.streaming and card.capabilities.push_notifications
asyncio.run(main())
print("Installed agent extras smoke passed on", sys.version.split()[0])
"""


def run(args: list[str], cwd: Path) -> None:
    subprocess.run(
        args,
        cwd=cwd,
        check=True,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    (wheel,) = args.dist.resolve().glob("*.whl")
    (source,) = args.dist.resolve().glob("*.tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for name in REQUIRED:
            if name not in names:
                raise SystemExit(f"wheel missing: {name}")
        if not any(name.endswith("/licenses/LICENSE") for name in names):
            raise SystemExit("wheel missing license")
        if any(Path(name).suffix == ".key" or name.endswith(".sqlite3") for name in names):
            raise SystemExit("private deployment material entered wheel")
    with tarfile.open(source) as archive:
        names = archive.getnames()
        for name in REQUIRED:
            if not any(entry.endswith("/src/" + name) for entry in names):
                raise SystemExit(f"sdist missing: {name}")
    with tempfile.TemporaryDirectory(prefix="checkedflow-installed-") as temporary:
        directory = Path(temporary)
        for artifact in (wheel, source):
            environment = directory / artifact.name
            run(["uv", "venv", "--python", args.python, str(environment)], directory)
            executable = environment / (
                "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
            )
            run(["uv", "pip", "install", "--python", str(executable), str(artifact)], directory)
            run([str(executable), "-I", "-c", SMOKE], directory)
            run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(executable),
                    str(artifact) + "[agents,distributed]",
                ],
                directory,
            )
            run([str(executable), "-I", "-c", AGENT_SMOKE], directory)
    manifest = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (wheel, source)
    }
    (args.dist / "SHA256SUMS.json").write_text(json.dumps(manifest, sort_keys=True, indent=2))
    print(json.dumps({"python": args.python, "artifacts": manifest}))


if __name__ == "__main__":
    main()
