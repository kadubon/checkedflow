"""Disposable local v2 laboratory; four nodes do not establish independent operators."""

import json
import sys
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from checkedflow.core.authority import Credential
from checkedflow.core.operational import genesis
from checkedflow.core.values import Object, obj, require
from checkedflow.distributed.cluster import Cluster as Processes
from checkedflow.distributed.operational_application import Configuration
from checkedflow.distributed.operational_client import Client
from checkedflow.identity import public_key
from checkedflow.operational_identity import sign_command
from checkedflow.wire import digest, dumps


class Cluster:
    """Reuse pinned process provisioning, then replace the unused v1 application genesis."""

    def __init__(self, directory: Path, cometbft: str, *, base_port: int = 29650) -> None:
        self.processes = Processes(directory, cometbft, base_port=base_port)
        self.directory = self.processes.directory
        self.succession_paths: tuple[Path, Path, Path] | None = None
        self.retention_paths: tuple[Path, ...] = ()
        self.keys: dict[tuple[str, int], Ed25519PrivateKey] = {}
        credentials = []
        validators = []
        for index in range(4):
            organization = f"org{index}"
            validators.append((organization, public_key(self.processes.keys[organization])))
            for identity, role in [
                (organization, "administrator"),
                (f"w{index}", "executor"),
                (f"p{index}", "producer"),
                (f"v{index}", "verifier"),
                (f"e{index}", "effect_executor"),
            ]:
                key = Ed25519PrivateKey.generate()
                self.keys[(identity, 1)] = key
                credentials.append(
                    Credential(
                        identity,
                        1,
                        organization,
                        role,
                        "" if role == "administrator" else "repository",
                        public_key(key),
                        0,
                    )
                )
                path = self.directory / f"node{index}" / f"{identity}.key"
                path.write_text(key.private_bytes_raw().hex(), encoding="ascii")
                path.chmod(0o600)
        self.initial = genesis(
            "checkedflow-operational-lab",
            "repository",
            tuple(f"org{i}" for i in range(4)),
            tuple(credentials),
        )
        self.configuration = Configuration(self.initial, tuple(validators))
        (self.directory / "operational.json").write_bytes(dumps(self.configuration.encode()))
        (self.directory / "genesis.json").write_bytes(dumps(self.configuration.encode()))
        for index in range(4):
            path = self.directory / f"node{index}" / "config/genesis.json"
            document = json.loads(path.read_text())
            document.update(chain_id=self.initial.chain, app_state=self.configuration.encode())
            path.write_text(json.dumps(document), encoding="utf-8")

    def client(self, index: int = 0) -> Client:
        return Client(f"http://127.0.0.1:{self.processes.port(index, 0)}", chain=self.initial.chain)

    def start_node(self, index: int) -> None:
        self.processes._spawn(
            f"app{index}",
            [
                sys.executable,
                "-m",
                "checkedflow.distributed.operational_application",
                "--configuration",
                str(self.directory / "operational.json"),
                "--database",
                str(self.directory / f"node{index}" / "operational.sqlite"),
                "--address",
                f"127.0.0.1:{self.processes.port(index, 2)}",
                *(
                    [
                        "--succession-manifest",
                        str(self.succession_paths[0]),
                        "--legacy-snapshot",
                        str(self.succession_paths[1]),
                        "--legacy-checkpoint",
                        str(self.succession_paths[2]),
                    ]
                    if self.succession_paths is not None
                    else []
                ),
                *(
                    ["--legacy-retention", str(self.retention_paths[index])]
                    if self.retention_paths
                    else []
                ),
            ],
        )
        self.processes.start_node(index, application=False)

    def start(self) -> None:
        for index in range(4):
            self.start_node(index)
        self.wait_height(2)

    def stop_node(self, index: int, *, crash: bool = False) -> None:
        self.processes.stop_node(index, crash=crash)

    def close(self) -> None:
        self.processes.close()

    def wait_height(
        self, height: int, *, nodes: tuple[int, ...] = (0, 1, 2, 3), seconds: int = 45
    ) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if all(self.client(index).state().height >= height for index in nodes):
                    return
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            time.sleep(0.1)
        raise TimeoutError("operational nodes did not reach required height")

    def send(self, kind: str, payload: Object, *, actor: str = "org0", node: int = 0) -> Object:
        state = self.client(node).state()
        scoped: Object = {"mission": state.mission}
        scoped.update(payload)
        command: Object = {
            "api_version": "checkedflow/v2",
            "chain": state.chain,
            "epoch": state.journal.epoch,
            "actor": actor,
            "revision": 1,
            "nonce": dict(state.journal.actors)[actor] + 1,
            "kind": kind,
            "payload": scoped,
        }
        command["id"] = f"{state.journal.epoch}:" + digest(command)[:48]
        identities = {"org0", "org1", "org2"} if actor == "org0" else {actor}
        raw = sign_command(
            command, {pair: key for pair, key in self.keys.items() if pair[0] in identities}
        )
        result = self.client(node).submit(raw)
        self.wait_height(int(str(result["height"])), nodes=(node,))
        return {"request": command["id"], "receipt": result}

    def common_hash(self) -> tuple[int, str]:
        height = min(self.client(index).state().height for index in range(4))
        hashes = []
        for index in range(4):
            block = obj(self.client(index).rpc("block", {"height": str(height)})["block"])
            hashes.append(str(obj(block["header"])["app_hash"]))
        require(len(set(hashes)) == 1, "DIVERGENCE", "operational application hashes differ")
        return height - 1, hashes[0]
