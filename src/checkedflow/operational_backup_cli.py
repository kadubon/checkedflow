"""Offline operator commands for application-only recovery; no validator startup."""

import argparse
import os
from pathlib import Path

from checkedflow.core.values import Object, require
from checkedflow.operational_backup import decode_checkpoint, export_history, restore_history
from checkedflow.operational_codec import MAX_STATE_BYTES, decode
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import Store
from checkedflow.wire import dumps


def configure(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="backup_action", required=True)
    inspect = actions.add_parser(
        "inspect-checkpoint", help="parse metadata; not trust verification"
    )
    inspect.add_argument("--checkpoint", required=True)
    export = actions.add_parser(
        "export", help="verify signed history and export to a new directory"
    )
    export.add_argument("--database", required=True)
    export.add_argument("--expected-hash", required=True)
    export.add_argument("--byte-limit", type=int, default=67_108_864)
    export.add_argument("--block-limit", type=int, default=100_000)
    restore = actions.add_parser("restore", help="replay into a new application directory")
    restore.add_argument("--source", required=True)
    restore.add_argument("--checkpoint", required=True)
    restore.add_argument("--current-height", type=int, required=True)
    for action in (export, restore):
        action.add_argument("--genesis", required=True, help="trusted v2 initial state JSON")
        action.add_argument("--destination", required=True, help="new private operator directory")


def _read(path: str, limit: int) -> bytes:
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "LIMIT", "operator input byte ceiling")
    return raw


def run(args: argparse.Namespace) -> Object:
    if args.backup_action == "inspect-checkpoint":
        return {
            "checkpoint": decode_checkpoint(_read(args.checkpoint, 4096)).record(),
            "authenticated": False,
            "backup_verified": False,
        }
    initial = decode(_read(args.genesis, MAX_STATE_BYTES))
    destination = Path(args.destination)
    if args.backup_action == "export":
        path = Path(args.database)
        require(path.is_file(), "UNAVAILABLE", "existing operator database required")
        store = Store(path, initial)
        destination.mkdir(mode=0o700)
        pending = destination / "pending.jsonl"
        with pending.open("xb") as output:
            checkpoint = export_history(
                store,
                output,
                expected_hash=args.expected_hash,
                byte_limit=args.byte_limit,
                block_limit=args.block_limit,
            )
            output.flush()
            os.fsync(output.fileno())
        pending.rename(destination / "history.jsonl")
        with (destination / "checkpoint.json").open("xb") as output:
            output.write(dumps(checkpoint.record()) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        return {"checkpoint": checkpoint.record(), "independent_custody_required": True}
    checkpoint = decode_checkpoint(_read(args.checkpoint, 4096))
    with Path(args.source).open("rb") as source:
        store = restore_history(
            source,
            destination,
            initial=initial,
            checkpoint=checkpoint,
            current_height=args.current_height,
        )
    state = store.load()
    return {
        "status": "application_restored",
        "height": state.height,
        "state_hash": Runtime(state).state_hash,
        "validator_started": False,
    }
