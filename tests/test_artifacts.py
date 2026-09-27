"""Real local artifact integrity, scope isolation and atomic-publication tests."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from checkedflow.artifacts import Access, LocalStore
from checkedflow.core.artifact import MAX_ARTIFACT_BYTES, Reference, reference
from checkedflow.core.values import Failure

ACCESS = Access(
    "fixture-operator", frozenset({"mission-a", "mission-b"}), frozenset({"read", "write"})
)


def ref(body: bytes = b"artifact", scope: str = "mission-a") -> Reference:
    return Reference(
        "sha256", sha256(body).hexdigest(), len(body), "text/plain", "evidence", scope, "1" * 64
    )


def test_reference_exact_contract_and_bounds() -> None:
    original = ref()
    assert reference(original.record()) == original
    for change in (
        {"algorithm": "etag"},
        {"version": "future"},
        {"digest": "Z" * 64},
        {"manifest": "x"},
        {"length": True},
        {"length": -1},
        {"length": MAX_ARTIFACT_BYTES + 1},
        {"scope": "../other"},
        {"scope": "A"},
        {"scope": ""},
        {"kind": "program"},
        {"content_type": "text/plain; charset=utf-8"},
    ):
        with pytest.raises(Failure):
            reference(original.record() | change)  # type: ignore[arg-type]
    with pytest.raises(Failure, match="SHAPE"):
        reference(original.record() | {"url": "https://invalid.example"})


def test_scope_isolation_reopen_and_zero_length(tmp_path: Path) -> None:
    path = tmp_path / "objects.sqlite"
    store = LocalStore(path)
    store.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    store = LocalStore(path)
    assert store.get(ref(), access=ACCESS) == b"artifact"
    with pytest.raises(Failure, match="UNAVAILABLE"):
        store.get(ref(scope="mission-b"), access=ACCESS)
    store.put(ref(scope="mission-b"), BytesIO(b"artifact"), access=ACCESS)
    assert store.get(ref(scope="mission-b"), access=ACCESS) == b"artifact"
    store.put(ref(b""), BytesIO(), access=ACCESS)
    assert store.get(ref(b""), access=ACCESS) == b""


def test_denial_before_storage_or_stream_observation(tmp_path: Path, monkeypatch: Any) -> None:
    store = LocalStore(tmp_path / "objects.sqlite")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unauthorized operation touched storage or source")

    class Source:
        read = forbidden

    monkeypatch.setattr(store, "_connect", forbidden)
    for access in (
        replace(ACCESS, scopes=frozenset({"mission-b"})),
        replace(ACCESS, permissions=frozenset()),
    ):
        with pytest.raises(Failure, match="AUTHORITY"):
            store.get(ref(), access=access)
        with pytest.raises(Failure, match="AUTHORITY"):
            store.put(ref(), Source(), access=access)  # type: ignore[arg-type]


def test_no_partial_visibility_and_bounded_stream_reads(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "objects.sqlite")

    class Interrupted:
        def read(self, size: int) -> bytes:
            with pytest.raises(Failure, match="UNAVAILABLE"):
                store.get(ref(), access=ACCESS)
            raise OSError("disconnected stream")

    with pytest.raises(OSError, match="disconnected"):
        store.put(ref(), Interrupted(), access=ACCESS)  # type: ignore[arg-type]
    for body in (b"art", b"artifact!", b"modified"):
        with pytest.raises(Failure, match="INTEGRITY"):
            store.put(ref(), BytesIO(body), access=ACCESS)
    with pytest.raises(Failure, match="UNAVAILABLE"):
        store.get(ref(), access=ACCESS)

    class BadStream:
        def __init__(self, result: Any) -> None:
            self.result = result

        def read(self, size: int) -> Any:
            return self.result

    for result, code in ((None, "SHAPE"), ("text", "SHAPE"), (b"x" * 20, "LIMIT")):
        with pytest.raises(Failure, match=code):
            store.put(ref(), BadStream(result), access=ACCESS)  # type: ignore[arg-type]

    class ShortReads(BytesIO):
        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 65_536
            return super().read(min(size, 8111))

    body = b"x" * MAX_ARTIFACT_BYTES
    store.put(ref(body), ShortReads(body), access=ACCESS)
    assert store.get(ref(body), access=ACCESS) == body


def test_concurrent_identical_puts_and_quota(tmp_path: Path) -> None:
    path = tmp_path / "objects.sqlite"
    store = LocalStore(path, scope_objects=1, scope_bytes=8)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: store.put(ref(), BytesIO(b"artifact"), access=ACCESS), range(12)))
    assert store.get(ref(), access=ACCESS) == b"artifact"
    store.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    with pytest.raises(Failure, match="CAPACITY"):
        store.put(ref(b"x"), BytesIO(b"x"), access=ACCESS)
    store.put(ref(scope="mission-b"), BytesIO(b"artifact"), access=ACCESS)
    small = LocalStore(tmp_path / "small.sqlite", scope_bytes=1)
    with pytest.raises(Failure, match="CAPACITY"):
        small.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    with pytest.raises(Failure, match="UNAVAILABLE"):
        small.get(ref(), access=ACCESS)
    with pytest.raises(Failure, match="CONFLICT"):
        LocalStore(path, scope_objects=2, scope_bytes=8)


def test_corruption_is_detected_not_silently_repaired(tmp_path: Path) -> None:
    path = tmp_path / "objects.sqlite"
    store = LocalStore(path)
    store.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    for corrupt in (b"modified", b"short", "artifact"):
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE artifact_objects SET body=?", (corrupt,))
        with pytest.raises(Failure, match="INTEGRITY"):
            store.get(ref(), access=ACCESS)
        with pytest.raises(Failure, match="INTEGRITY"):
            store.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM artifact_objects")
    with pytest.raises(Failure, match="UNAVAILABLE"):
        store.get(ref(), access=ACCESS)


def test_sql_failure_rolls_back_publication(tmp_path: Path) -> None:
    path = tmp_path / "objects.sqlite"
    store = LocalStore(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TRIGGER abort_publication AFTER INSERT ON artifact_objects "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        store.put(ref(), BytesIO(b"artifact"), access=ACCESS)
    with pytest.raises(Failure, match="UNAVAILABLE"):
        LocalStore(path).get(ref(), access=ACCESS)


def test_wrong_database_missing_identity_and_unavailable_store(tmp_path: Path) -> None:
    path = tmp_path / "unrelated.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(Failure, match="VERSION"):
        LocalStore(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    path = tmp_path / "objects.sqlite"
    store = LocalStore(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM artifact_identity")
    with pytest.raises(Failure, match="STORAGE"):
        LocalStore(path)
    store.path = tmp_path / "missing" / "unavailable.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        store.get(ref(), access=ACCESS)


def test_portable_schema_agrees_with_reference() -> None:
    schema = json.loads(
        files("checkedflow").joinpath("data/artifact-reference.schema.json").read_text()
    )
    validator = Draft202012Validator(schema)
    for body in (b"", b"artifact"):
        validator.validate(ref(body).record())
    for field, value in (
        ("length", True),
        ("scope", "../other"),
        ("digest", "X" * 64),
        ("digest", "1" * 64 + "\n"),
        ("manifest", "1" * 64 + "\n"),
        ("scope", "mission-a\n"),
    ):
        assert list(validator.iter_errors(ref().record() | {field: value}))
        with pytest.raises(Failure):
            reference(ref().record() | {field: value})


def test_concurrent_different_puts_cannot_overdraw_quota(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "quota.sqlite", scope_objects=1)

    def put(index: int) -> str:
        body = str(index).encode()
        try:
            store.put(ref(body), BytesIO(body), access=ACCESS)
            return "OK"
        except Failure as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(put, range(12)))
    assert results.count("OK") == 1
    assert results.count("CAPACITY") == 11
