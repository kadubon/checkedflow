"""Small JSON-oriented command line interface. Runtime extras are imported on demand."""

import argparse
import os
import sqlite3
import sys
from importlib.resources import files
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from checkedflow import __version__
from checkedflow.contracts import check
from checkedflow.core.values import JSON, Failure, Object, require
from checkedflow.identity import public_key, sign
from checkedflow.recovery import read_blocks, replay_blocks
from checkedflow.serialization import decode, encode
from checkedflow.synthesis import request
from checkedflow.wire import document, dumps


def emit(value: JSON) -> None:
    sys.stdout.buffer.write(dumps(value) + b"\n")


def key(path: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(Path(path).read_text().strip()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="checkedflow", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="action", required=True)
    from checkedflow.operational_backup_cli import configure as backup_arguments

    backup_arguments(commands.add_parser("application-backup"))
    for name in ("generator", "example"):
        commands.add_parser(name)
    resource = commands.add_parser("schema")
    resource.add_argument(
        "name",
        choices=[
            "envelope",
            "state",
            "generator",
            "block",
            "commands",
            "vectors",
            "research",
            "agents",
            "agent-request",
            "agent-vectors",
            "callback-keyring",
            "access-policy",
            "access-roles",
            "access-vectors",
            "artifact-publication",
        ],
    )
    keys = commands.add_parser("keygen")
    keys.add_argument("--output", required=True)
    signer = commands.add_parser("sign")
    signer.add_argument("--command", required=True)
    signer.add_argument("--key", action="append", required=True, help="identity=private-key-file")
    signer.add_argument("--output", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("envelope")
    replay = commands.add_parser("replay")
    replay.add_argument("--genesis", required=True)
    archive = replay.add_mutually_exclusive_group(required=True)
    archive.add_argument("--blocks", help="bounded JSON object containing a blocks array")
    archive.add_argument("--blocks-jsonl", help="one bounded block object per line")
    abci = commands.add_parser("abci")
    abci.add_argument("--genesis", required=True)
    abci.add_argument("--database", required=True)
    abci.add_argument("--listen", default="127.0.0.1:26658")
    worker = commands.add_parser("worker")
    worker.add_argument("--rpc", required=True)
    worker.add_argument("--identity", required=True)
    worker.add_argument("--key", required=True)
    worker.add_argument("--chain", required=True)
    state = commands.add_parser("state")
    state.add_argument("--rpc", required=True)
    for transport in ("a2a", "mcp"):
        agent = commands.add_parser(transport)
        agent.add_argument("--rpc", required=True, help="operator-owned loopback full node")
        agent.add_argument("--chain", required=True)
        agent.add_argument("--mission", required=True)
        agent.add_argument("--protocol", choices=["v1", "v2"], default="v1")
        agent.add_argument("--host", default="127.0.0.1")
        agent.add_argument("--port", type=int, default=8080 if transport == "a2a" else 8082)
        agent.add_argument("--token-env", default="CHECKEDFLOW_AGENT_TOKEN")
        agent.add_argument(
            "--access-policy", help="private operator mission/client grants; required for v2"
        )
        agent.add_argument("--artifact-catalog", help="protected mission publication catalog")
        agent.add_argument("--artifact-store", help="private local artifact database")
        agent.add_argument("--tls-cert-file", help="operator-provisioned server certificate chain")
        agent.add_argument("--tls-key-file", help="private server TLS key")
        agent.add_argument("--tls-client-ca", help="CA bundle required for client certificates")
        agent.add_argument("--oauth-issuer", default="")
        agent.add_argument("--oauth-audience", default="")
        agent.add_argument("--oauth-jwks", default="", help="operator-managed public JWKS file")
        if transport == "a2a":
            agent.add_argument("--advertised-url", default="", help="explicit HTTPS proxy /rpc URL")
            agent.add_argument(
                "--grpc-advertised-url", default="", help="explicit HTTPS gRPC proxy URL"
            )
            agent.add_argument("--callback-key-file", help="private operator callback keyring JSON")
            agent.add_argument("--journal", required=True, help="private SQLite transport journal")
            agent.add_argument(
                "--push-host",
                action="append",
                default=[],
                help="allowlisted HTTPS callback hostname",
            )
            agent.add_argument(
                "--grpc-port", type=int, default=0, help="optional loopback gRPC port"
            )
        else:
            agent.add_argument("--transport", choices=["stdio", "http", "sse"], default="stdio")
    demo = commands.add_parser("demo")
    demo.add_argument("--directory", required=True)
    demo.add_argument("--image", required=True, help="local Python OCI image with sha256 digest")
    demo.add_argument("--cometbft", default="cometbft")
    args = parser.parse_args(argv)
    try:
        if args.action == "application-backup":
            from checkedflow.operational_backup_cli import run as backup_command

            emit(backup_command(args))
        elif args.action == "schema":
            filename = {
                "envelope": "envelope.schema.json",
                "state": "state.schema.json",
                "generator": "generator.schema.json",
                "block": "block.schema.json",
                "commands": "commands.json",
                "vectors": "vectors.json",
                "research": "research.json",
                "agents": "agents.json",
                "agent-request": "agent-request.schema.json",
                "agent-vectors": "agent-vectors.json",
                "callback-keyring": "callback-keyring.schema.json",
                "artifact-publication": "artifact-publication.schema.json",
                "access-policy": "access-policy.schema.json",
                "access-roles": "access-roles.json",
                "access-vectors": "access-vectors.json",
            }[args.name]
            sys.stdout.buffer.write(files("checkedflow").joinpath("data", filename).read_bytes())
        elif args.action in {"generator", "example"}:
            value: Object = (
                document(sys.stdin.buffer.read(1048577))
                if args.action == "generator"
                else {
                    "target": ["double", "increment"],
                    "max_candidates": 256,
                    "max_depth": 3,
                    "library": [],
                }
            )
            emit(
                request(value)
                if args.action == "generator"
                else {"verification": "not_executed", "candidate": request(value)}
            )
        elif args.action == "keygen":
            private = Ed25519PrivateKey.generate()
            with Path(args.output).open("x", encoding="ascii") as output:
                output.write(private.private_bytes_raw().hex() + "\n")
            Path(args.output).chmod(0o600)
            emit({"public_key": public_key(private)})
        elif args.action == "sign":
            keys_by_id = {
                identity: key(path) for identity, path in (item.split("=", 1) for item in args.key)
            }
            envelope = sign(document(Path(args.command).read_bytes()), keys_by_id)
            check(envelope)
            Path(args.output).write_bytes(dumps(envelope) + b"\n")
        elif args.action == "validate":
            check(document(Path(args.envelope).read_bytes()))
            emit({"schema": "valid", "signatures": "not_verified_without_genesis_state"})
        elif args.action == "replay":
            runtime = replay_blocks(
                decode(document(Path(args.genesis).read_bytes())),
                read_blocks(Path(args.blocks or args.blocks_jsonl), jsonl=bool(args.blocks_jsonl)),
            )
            emit({"height": runtime.state.height, "hash": runtime.state_hash})
        elif args.action == "abci":
            from checkedflow.distributed.application import serve

            serve(
                Path(args.database), decode(document(Path(args.genesis).read_bytes())), args.listen
            )
        elif args.action in {"worker", "state"}:
            from checkedflow.distributed.client import Client

            client = Client(args.rpc)
            if args.action == "state":
                emit(encode(client.state()))
            else:
                from checkedflow.worker import Worker

                emit(Worker(client, args.identity, key(args.key), args.chain).once())
        elif args.action in {"a2a", "mcp"}:
            from checkedflow.agents.access import Policy
            from checkedflow.agents.gateway import AgentGateway, Gateway
            from checkedflow.agents.oauth import OAuth
            from checkedflow.distributed.client import Client

            require(
                args.protocol != "v2" or bool(args.access_policy),
                "ACCESS",
                "v2 requires --access-policy",
            )
            require(
                not args.access_policy or args.protocol == "v2",
                "ACCESS",
                "client policy requires v2",
            )
            policy = (
                Policy(Path(args.access_policy), args.chain, args.mission)
                if args.access_policy
                else None
            )
            require(
                bool(args.oauth_issuer) == bool(args.oauth_audience) == bool(args.oauth_jwks),
                "AUTH",
                "supply all OAuth settings",
            )
            oauth = (
                OAuth(args.oauth_issuer, args.oauth_audience, Path(args.oauth_jwks))
                if args.oauth_issuer
                else None
            )
            require(
                bool(args.artifact_catalog) == bool(args.artifact_store),
                "ACCESS",
                "supply both artifact catalog and store",
            )
            artifacts = None
            if args.artifact_catalog:
                from checkedflow.agents.download import Reader
                from checkedflow.artifacts import LocalStore

                require(policy is not None, "ACCESS", "artifact downloads require client policy")
                require(
                    Path(args.artifact_store).is_file(), "UNAVAILABLE", "artifact store missing"
                )
                if policy is not None:
                    artifacts = Reader(
                        LocalStore(Path(args.artifact_store)), Path(args.artifact_catalog), policy
                    )
            from checkedflow.agents.tls import MutualTLS

            require(
                bool(args.tls_cert_file) == bool(args.tls_key_file) == bool(args.tls_client_ca),
                "TLS",
                "supply certificate, key and client CA together",
            )
            require(
                not args.tls_cert_file or args.action != "mcp" or args.transport != "stdio",
                "TLS",
                "stdio uses process ownership, not TLS",
            )
            tls = (
                MutualTLS(
                    Path(args.tls_cert_file), Path(args.tls_key_file), Path(args.tls_client_ca)
                )
                if args.tls_cert_file
                else None
            )
            gateway: AgentGateway
            if args.protocol == "v2":
                from checkedflow.agents.operational_gateway import Gateway as OperationalGateway
                from checkedflow.distributed.operational_client import Client as OperationalClient

                gateway = OperationalGateway(
                    OperationalClient(args.rpc, chain=args.chain),
                    args.chain,
                    args.mission,
                    administration=policy is not None,
                )
            else:
                gateway = Gateway(Client(args.rpc), args.chain, args.mission)
            gateway.state()
            if args.action == "a2a":
                from checkedflow.agents.a2a import serve as serve_a2a
                from checkedflow.agents.secrets import Keyring

                serve_a2a(
                    gateway,
                    args.host,
                    args.port,
                    os.environ.get(args.token_env, ""),
                    journal_path=Path(args.journal),
                    push_hosts=tuple(args.push_host),
                    grpc_port=args.grpc_port,
                    policy=policy,
                    artifacts=artifacts,
                    tls=tls,
                    advertised_url=args.advertised_url,
                    grpc_advertised_url=args.grpc_advertised_url,
                    oauth=oauth,
                    callback_keys=(
                        Keyring.load(Path(args.callback_key_file))
                        if args.callback_key_file
                        else None
                    ),
                )
            else:
                from checkedflow.agents.mcp import create_server

                if args.transport == "stdio":
                    require(
                        oauth is None,
                        "AUTH",
                        "OAuth is an HTTP transport; stdio uses process ownership",
                    )
                    create_server(gateway, policy=policy, artifacts=artifacts).run(
                        transport="stdio"
                    )
                else:
                    from checkedflow.agents.mcp import serve as serve_mcp

                    serve_mcp(
                        gateway,
                        args.host,
                        args.port,
                        os.environ.get(args.token_env, ""),
                        transport=args.transport,
                        oauth_issuer=args.oauth_issuer,
                        oauth_audience=args.oauth_audience,
                        oauth_jwks=args.oauth_jwks,
                        policy=policy,
                        artifacts=artifacts,
                        tls=tls,
                    )
        elif args.action == "demo":
            from checkedflow.distributed.demo import demonstrate

            emit(demonstrate(Path(args.directory), args.image, args.cometbft))
        return 0
    except (Failure, OSError, ValueError, ImportError, sqlite3.Error) as exc:
        error = (
            exc.code
            if isinstance(exc, Failure)
            else (
                "DEPENDENCY_UNAVAILABLE"
                if isinstance(exc, ImportError)
                else "STORAGE"
                if isinstance(exc, sqlite3.Error)
                else "IO"
                if isinstance(exc, OSError)
                else "INVALID_INPUT"
            )
        )
        sys.stderr.buffer.write(dumps({"error": error, "message": str(exc)}) + b"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
