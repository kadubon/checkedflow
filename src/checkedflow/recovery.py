"""Replay bounded block records, including empty blocks and rejected transactions."""

from collections.abc import Iterable, Iterator
from pathlib import Path

from checkedflow.core.model import State
from checkedflow.core.values import Failure, Object, array, fields, integer, obj, require, text
from checkedflow.runtime import Runtime
from checkedflow.wire import MAX_BYTES, document, transaction_document


def read_blocks(path: Path, *, jsonl: bool = False) -> Iterator[Object]:
    """JSONL bounds each record, without imposing a limit on total archive length."""
    with path.open("rb") as stream:
        if jsonl:
            while raw := stream.readline(MAX_BYTES + 1):
                yield document(raw, string_limit=2097152)
        else:
            archive = document(stream.read(MAX_BYTES + 1), string_limit=2097152)
            fields(archive, "blocks")
            yield from (obj(block) for block in array(archive["blocks"], limit=100000))


def replay_blocks(initial: State, blocks: Iterable[Object]) -> Runtime:
    """Check journal outcomes; the caller is responsible for authenticating block order."""
    runtime = Runtime(initial)
    for block in blocks:
        fields(block, "height transactions")
        height = integer(block["height"], low=1)
        require(height == runtime.state.height + 1, "HEIGHT", "block gap")
        runtime.tick(height)
        # Even rejected one-byte transactions can appear in a bounded consensus block.
        for raw_tx in array(block["transactions"], limit=2097152):
            transaction = obj(raw_tx)
            fields(transaction, "raw code")
            raw = "" if transaction["raw"] == "" else text(transaction["raw"], limit=2097152)
            require(
                len(raw) % 2 == 0 and all(c in "0123456789abcdef" for c in raw),
                "REPLAY",
                "transaction bytes must be lowercase hexadecimal",
            )
            try:
                runtime.apply(transaction_document(bytes.fromhex(raw)), height=height)
                code = "OK"
            except Failure as exc:
                code = exc.code
            require(code == transaction["code"], "REPLAY", "recorded outcome differs")
    return runtime
