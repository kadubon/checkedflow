"""A local four-node laboratory. Production organizations provision their own hosts."""

import base64
import json
import shutil
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import IO, cast

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from checkedflow.core.model import genesis
from checkedflow.core.values import Object, obj, require
from checkedflow.distributed.client import Client
from checkedflow.identity import public_key, sign
from checkedflow.serialization import encode
from checkedflow.wire import digest, dumps


class Cluster:
    """Own only subprocesses and files beneath a previously nonexistent directory."""

    def __init__(self, directory: Path, cometbft: str, *, base_port: int = 28650) -> None:
        executable = shutil.which(cometbft)
        require(executable is not None, "COMETBFT", "CometBFT executable unavailable")
        executable = cast(str, executable)
        self.executable = executable
        self.directory = directory.resolve()
        require(not self.directory.exists(), "DIRECTORY", "laboratory directory already exists")
        self.directory.mkdir(parents=True)
        self.base_port = base_port
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.logs: list[IO[bytes]] = []
        self.keys: dict[str, Ed25519PrivateKey] = {}
        version = self._run(["version"]).strip()
        if version != "0.40.0":
            # The upstream v0.40.0 Go module retains a stale 0.39.0 CLI constant.
            # Check the embedded Go module identity and checksum, not a guessed version alias.
            marker = (
                b"\nmod\tgithub.com/cometbft/cometbft\tv0.40.0\t"
                b"h1:0+zc7FlcnonFfMwzhyDCd5vK/fHZqz0RfRyTCxZsFPU=\n"
            )
            require(
                marker in Path(executable).read_bytes(),
                "VERSION",
                "CometBFT v0.40.0 module required",
            )
        validators: list[Object] = []
        peers: list[str] = []
        for index in range(4):
            home = self.directory / f"node{index}"
            self._run(["init", "--home", str(home)])
            private = json.loads((home / "config/priv_validator_key.json").read_text())
            self.keys[f"org{index}"] = Ed25519PrivateKey.from_private_bytes(
                base64.b64decode(private["priv_key"]["value"])[:32]
            )
            validators.append(
                {
                    "address": private["address"],
                    "pub_key": private["pub_key"],
                    "power": "10",
                    "name": f"org{index}",
                }
            )
            node_id = self._run(["show-node-id", "--home", str(home)]).strip()
            peers.append(f"{node_id}@127.0.0.1:{self.port(index, 1)}")
            worker = Ed25519PrivateKey.generate()
            self.keys[f"w{index}"] = worker
            for identity in (f"org{index}", f"w{index}"):
                path = home / f"{identity}.key"
                path.write_text(self.keys[identity].private_bytes_raw().hex(), encoding="ascii")
                path.chmod(0o600)
        self.initial = genesis(
            "checkedflow-lab", {f"org{i}": public_key(self.keys[f"org{i}"]) for i in range(4)}
        )
        (self.directory / "genesis.json").write_bytes(dumps(encode(self.initial)))
        template = json.loads((self.directory / "node0/config/genesis.json").read_text())
        template.update(
            chain_id=self.initial.chain, validators=validators, app_state=encode(self.initial)
        )
        # RPC transaction transport and ABCI are bounded independently of the block limit.
        template["consensus_params"]["block"]["max_bytes"] = "2097152"
        for index in range(4):
            home = self.directory / f"node{index}"
            (home / "config/genesis.json").write_text(json.dumps(template), encoding="utf-8")
            config = home / "config/config.toml"
            content = config.read_text()
            replacements = {
                'proxy_app = "tcp://127.0.0.1:26658"': (
                    f'proxy_app = "passthrough:///127.0.0.1:{self.port(index, 2)}"'
                ),
                'abci = "socket"': 'abci = "grpc"',
                'laddr = "tcp://127.0.0.1:26657"': (
                    f'laddr = "tcp://127.0.0.1:{self.port(index, 0)}"'
                ),
                'laddr = "tcp://0.0.0.0:26656"': f'laddr = "tcp://127.0.0.1:{self.port(index, 1)}"',
                'persistent_peers = ""': 'persistent_peers = "'
                + ",".join(p for j, p in enumerate(peers) if j != index)
                + '"',
                "addr_book_strict = true": "addr_book_strict = false",
                "allow_duplicate_ip = false": "allow_duplicate_ip = true",
                'timeout_commit = "1s"': 'timeout_commit = "200ms"',
                'timeout_propose = "3s"': 'timeout_propose = "1s"',
                'timeout_broadcast_tx_commit = "10s"': 'timeout_broadcast_tx_commit = "20s"',
                "max_tx_bytes = 4194304": "max_tx_bytes = 1048576",
                "max_body_bytes = 1000000": "max_body_bytes = 2097152",
                "create_empty_blocks = true": "create_empty_blocks = true",
            }
            for old, new in replacements.items():
                require(old in content, "CONFIG", f"unsupported CometBFT config: {old}")
                content = content.replace(old, new)
            config.write_text(content, encoding="utf-8")

    def _run(self, arguments: list[str]) -> str:
        result = subprocess.run(
            [self.executable, *arguments], check=True, capture_output=True, timeout=30
        )  # nosec B603
        return result.stdout.decode()

    def port(self, index: int, service: int) -> int:
        return self.base_port + 10 * index + service

    def client(self, index: int = 0) -> Client:
        return Client(f"http://127.0.0.1:{self.port(index, 0)}")

    def _spawn(self, name: str, command: list[str]) -> None:
        require(name not in self.processes, "PROCESS", "process already owned")
        log = (self.directory / f"{name}.log").open("ab")
        self.logs.append(log)
        self.processes[name] = subprocess.Popen(
            command, stdout=log, stderr=log, start_new_session=True
        )  # nosec B603

    def start_node(self, index: int, *, application: bool = True) -> None:
        home = self.directory / f"node{index}"
        if application:
            self._spawn(
                f"app{index}",
                [
                    sys.executable,
                    "-m",
                    "checkedflow.cli",
                    "abci",
                    "--genesis",
                    str(self.directory / "genesis.json"),
                    "--database",
                    str(home / "application.sqlite3"),
                    "--listen",
                    f"127.0.0.1:{self.port(index, 2)}",
                ],
            )
        self._spawn(f"comet{index}", [self.executable, "start", "--home", str(home)])

    def start(self) -> None:
        for index in range(4):
            self.start_node(index)
        self.wait_height(2)

    def stop_process(self, name: str, *, crash: bool = False) -> None:
        process = self.processes.pop(name, None)
        if process is not None:
            (process.kill if crash else process.terminate)()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def stop_node(self, index: int, *, crash: bool = False) -> None:
        self.stop_process(f"comet{index}", crash=crash)
        self.stop_process(f"app{index}", crash=crash)

    def close(self) -> None:
        for name in list(self.processes):
            self.stop_process(name)
        for log in self.logs:
            log.close()

    def wait_height(
        self, height: int, *, nodes: tuple[int, ...] = (0, 1, 2, 3), seconds: int = 45
    ) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if all(self.client(i).state().height >= height for i in nodes):
                    return
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            time.sleep(0.1)
        raise TimeoutError(f"nodes did not reach height {height}; see {self.directory}/*.log")

    def send(self, kind: str, payload: Object, *, actor: str = "org0", node: int = 0) -> Object:
        state = self.client(node).state()
        command: Object = {
            "api_version": "checkedflow/v1",
            "chain": state.chain,
            "nonce": state.nonces.get(actor, 0) + 1,
            "actor": actor,
            "kind": kind,
            "payload": payload,
        }
        command["id"] = digest(command)
        signers = ("org0", "org1", "org2") if actor == "org0" else (actor,)
        return self.client(node).submit(sign(command, {name: self.keys[name] for name in signers}))

    def common_hash(self) -> tuple[int, str]:
        """Compare a common committed block's app hash, avoiding races with new empty blocks."""
        heights = [self.client(i).state().height for i in range(4)]
        height = min(heights)
        hashes = [
            obj(obj(self.client(i).rpc("block", {"height": str(height)}))["block"])
            for i in range(4)
        ]
        fingerprints = [str(obj(block["header"])["app_hash"]) for block in hashes]
        require(len(set(fingerprints)) == 1, "DIVERGENCE", "committed application hashes differ")
        return height - 1, fingerprints[0]
