"""Portable bytes, AST provenance, persistence and isolation configuration contracts."""

import ast
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from conftest import IMAGE

from checkedflow.core.values import Failure
from checkedflow.runner import GVisorRunner, Limits, command
from checkedflow.serialization import encode
from checkedflow.storage import Store
from checkedflow.synthesis import LibraryEntry, synthesize
from checkedflow.wire import dumps, loads


@pytest.mark.parametrize(
    "raw, code",
    [
        ('{"a":0,"a":1}', "DUPLICATE_KEY"),
        ('{"a":{"x":0,"x":1}}', "DUPLICATE_KEY"),
        ("1.0", "NUMBER"),
        ("1e0", "NUMBER"),
        ("NaN", "NUMBER"),
        ("Infinity", "NUMBER"),
        ("9007199254740992", "NUMBER"),
        ("-9007199254740992", "NUMBER"),
        ('"\\ud800"', "UNICODE"),
        (b'"\xff"', "JSON"),
        ("{", "JSON"),
        ("[" * 40 + "0" + "]" * 40, "LIMIT"),
    ],
)
def test_reject_ambiguous_wire_values(raw, code):
    with pytest.raises(Failure, match=code):
        loads(raw)


def test_canonical_order_and_integer_boundaries():
    assert dumps({"z": 0, "a": 9007199254740991}) == b'{"a":9007199254740991,"z":0}'
    # RFC 8785 sorts UTF-16 code units, unlike Python's default codepoint order.
    assert dumps({"\ue000": 1, "\U00010000": 2}).decode() == '{"\U00010000":2,"\ue000":1}'
    assert loads("-0") == 0
    assert dumps(loads("-0")) == b"0"


def test_three_generations_embed_admitted_source_with_nested_names():
    first = synthesize(("double",))
    second = synthesize(
        ("double", "increment"), library=(LibraryEntry("first", first.operations, first.source),)
    )
    third = synthesize(
        ("double", "increment", "reverse"),
        library=(LibraryEntry("second", second.operations, second.source),),
    )
    assert second.dependencies == ("first",) and third.dependencies == ("second",)
    tree = ast.parse(third.source)
    assert len([node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]) == 5
    assert third.candidates_tried < synthesize(("double", "increment", "reverse")).candidates_tried
    # Execution correctness is covered by the real gVisor acceptance suite, never host eval.


def test_search_bounds_and_untrusted_library():
    with pytest.raises(Failure, match="SEARCH_EXHAUSTED"):
        synthesize(("double", "increment"), max_candidates=1)
    with pytest.raises(Failure, match="GRAMMAR"):
        synthesize(("double",), library=(LibraryEntry("bad", ("double",), "import os\n"),))


def test_atomic_store_recovery_and_conflicting_commit(h, tmp_path):
    store = Store(tmp_path / "node.sqlite3", h.initial)
    runtime = h.runtime.__class__(h.initial)
    for envelope, height in h.events:
        runtime.apply(envelope, height=height)
        txs = [{"raw": dumps(envelope).hex(), "code": "OK"}]
        store.commit(runtime.state, txs)
        store.commit(runtime.state, txs)
    assert store.load() == runtime.state
    assert Store(store.path, h.initial).load() == runtime.state
    with pytest.raises(Failure, match="STORAGE"):
        store.commit(runtime.state, [])
    with closing(sqlite3.connect(store.path)) as db, pytest.raises(RuntimeError), db:
        db.execute("UPDATE snapshot SET hash='corrupt'")
        raise RuntimeError("crash before commit")
    assert encode(store.load()) == encode(runtime.state)
    assert len(store.blocks()) == len(h.events)


def test_sandbox_argv_is_closed_and_has_no_host_fallback(tmp_path):
    args = command(IMAGE, tmp_path, "owned", ("python", "-c", "print('x')"), Limits())
    for flag in [
        "--runtime=runsc",
        "--network=none",
        "--read-only",
        "--user=65534:65534",
        "--pull=never",
        "--cap-drop=ALL",
        "--pids-limit=128",
    ]:
        assert flag in args
    assert "readonly" in args[args.index("--mount") + 1]
    assert args[-2:] == ["-c", "print('x')"]
    with pytest.raises(Failure, match="IMAGE"):
        command("python:latest", Path("."), "owned", ("python",), Limits())
    with pytest.raises(Failure, match="LIMIT"):
        command(IMAGE, Path("."), "owned", ("python",), Limits(seconds=0))


def test_packaged_vectors():
    from importlib.resources import files

    vectors = json.loads(files("checkedflow").joinpath("data/vectors.json").read_text())
    for row in vectors["canonical"]:
        assert dumps(loads(row["input"])).hex() == row["canonical_hex"]
    for row in vectors["invalid"]:
        with pytest.raises(Failure, match=row["error"]):
            loads(row["input"])


def test_missing_isolation_runtime_never_dispatches_candidate(monkeypatch):
    import subprocess

    import checkedflow.runner as adapter

    monkeypatch.setattr(adapter.shutil, "which", lambda _name: "/trusted/docker")
    monkeypatch.setattr(adapter.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 0, b'{"runc":{}}', b""),
    )
    monkeypatch.setattr(
        adapter.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("candidate dispatched")
    )
    with pytest.raises(Failure, match="SANDBOX_UNAVAILABLE"):
        GVisorRunner(IMAGE).run(("python", "-c", "print(1)"), {}, None)


def test_daemon_probe_timeout_is_a_stable_unavailable_result(monkeypatch):
    import subprocess

    import checkedflow.runner as adapter

    monkeypatch.setattr(adapter.shutil, "which", lambda _name: "/trusted/docker")
    monkeypatch.setattr(adapter.platform, "system", lambda: "Linux")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("docker info", 10)

    monkeypatch.setattr(adapter.subprocess, "run", timeout)
    monkeypatch.setattr(
        adapter.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("candidate dispatched")
    )
    with pytest.raises(Failure, match="SANDBOX_UNAVAILABLE"):
        GVisorRunner(IMAGE).run(("python", "-c", "print(1)"), {}, None)
