"""Maximum valid source documents must survive persistence, RPC and standalone replay."""

import base64
import json
import subprocess
import sys

import httpx
import pytest

from checkedflow.core.values import Failure
from checkedflow.distributed.client import Client
from checkedflow.runtime import Runtime
from checkedflow.serialization import encode
from checkedflow.storage import Store
from checkedflow.wire import dumps


def test_hex_log_does_not_inherit_source_string_limit(h, tmp_path):
    store = Store(tmp_path / "state.sqlite3", h.initial)
    runtime = Runtime(h.initial)
    runtime.tick(1)
    raw = "ab" * 200000
    store.commit(runtime.state, [{"raw": raw, "code": "JSON"}])
    assert store.blocks()[0]["transactions"][0]["raw"] == raw


def test_rpc_base64_snapshot_can_exceed_a_source_string(h, monkeypatch):
    h.task(spec={"target": ["double"], "context": "x" * 210000})
    encoded = base64.b64encode(dumps(encode(h.runtime.state))).decode()
    assert len(encoded) > 262144
    reply = httpx.Response(
        200,
        content=json.dumps({"result": {"response": {"code": 0, "value": encoded}}}),
        request=httpx.Request("POST", "http://127.0.0.1:26657"),
    )
    monkeypatch.setattr(httpx.Client, "post", lambda *_a, **_kw: reply)
    assert Client("http://127.0.0.1:26657").state() == h.runtime.state


def test_rpc_submission_can_transport_a_large_bounded_envelope(h, monkeypatch):
    envelope = h.envelope(
        "task.create",
        {
            "id": "large",
            "mission": "m",
            "phase": "generate",
            "spec": {"target": ["double"], "context": ["x" * 250000] * 3},
            "dependencies": [],
            "cost": 1,
            "ttl": 100,
            "effect": "isolated",
        },
    )

    def post(_self, url, *, content, headers):
        assert len(content) > 1000000
        assert base64.b64decode(json.loads(content)["params"]["tx"]) == dumps(envelope)
        return httpx.Response(
            200,
            json={"result": {"check_tx": {"code": 0}, "tx_result": {"code": 0}}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", post)
    Client("http://127.0.0.1:26657").submit(envelope)


def test_cli_replay_checks_logged_outcomes_and_hash(h, tmp_path):
    store = Store(tmp_path / "state.sqlite3", h.initial)
    runtime = Runtime(h.initial)
    for envelope, height in h.events:
        runtime.apply(envelope, height=height)
        store.commit(runtime.state, [{"raw": dumps(envelope).hex(), "code": "OK"}])
    (tmp_path / "genesis.json").write_bytes(dumps(encode(h.initial)))
    (tmp_path / "blocks.json").write_bytes(dumps({"blocks": store.blocks()}))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "checkedflow.cli",
            "replay",
            "--genesis",
            str(tmp_path / "genesis.json"),
            "--blocks",
            str(tmp_path / "blocks.json"),
        ],
        capture_output=True,
        check=True,
        cwd=tmp_path,
    )
    assert json.loads(result.stdout)["hash"] == runtime.state_hash


@pytest.mark.parametrize(
    "url",
    ["http://example.org", "https://127.0.0.1", "http://192.0.2.1", "http://127.0.0.1/?bad=1"],
)
def test_public_or_ambiguous_rpc_is_rejected(url):
    with pytest.raises(Failure):
        Client(url)


def test_argument_vectors_preserve_repeated_values(h, tmp_path):
    from conftest import IMAGE

    state = h.apply(
        "verifier.register",
        {
            "id": "another",
            "contract": "other/v1",
            "image": IMAGE,
            "argv": ["python", "-c", "x", "x"],
            "exhaustive": False,
        },
        admin=True,
    )
    store = Store(tmp_path / "arguments.sqlite3", state)
    assert store.load().verifiers["another"].argv == ("python", "-c", "x", "x")


def test_streaming_recovery_keeps_empty_blocks_and_many_rejections(h, tmp_path):
    from checkedflow.recovery import read_blocks, replay_blocks

    blocks = [
        {"height": 1, "transactions": [{"raw": "", "code": "JSON"}] * 1100},
        {"height": 2, "transactions": []},
    ]
    path = tmp_path / "blocks.jsonl"
    path.write_bytes(b"\n".join(dumps(block) for block in blocks) + b"\n")
    runtime = replay_blocks(h.initial, read_blocks(path, jsonl=True))
    assert runtime.state.height == 2 and not runtime.state.processed
    store = Store(tmp_path / "journal.sqlite3", h.initial)
    store.commit(Runtime(h.initial).tick(1), blocks[0]["transactions"])
    store.commit(runtime.state, [])
    assert list(store.iter_blocks()) == blocks


@pytest.mark.parametrize(
    "block,code",
    [
        ({"height": 2, "transactions": []}, "HEIGHT"),
        ({"height": 1, "transactions": [{"raw": "GG", "code": "JSON"}]}, "REPLAY"),
        ({"height": 1, "transactions": [{"raw": "", "code": "OK"}]}, "REPLAY"),
        ({"height": 1, "transactions": [], "unknown": 0}, "SHAPE"),
    ],
)
def test_recovery_rejects_ambiguous_or_false_journal_records(h, block, code):
    from checkedflow.recovery import replay_blocks

    with pytest.raises(Failure, match=code):
        replay_blocks(h.initial, [block])
