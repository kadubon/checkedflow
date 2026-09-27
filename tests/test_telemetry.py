"""Local observations are bounded, secret-free and independent of committed work."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.resources import files
from io import StringIO

import pytest
from jsonschema import Draft202012Validator
from opentelemetry import trace as otel
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_effect_supervisor import Executor
from test_observability import fixture
from test_worker_supervisor import Worker

from checkedflow.core.values import Failure
from checkedflow.telemetry import MAX_BATCH, Record, Recorder, trace, write_json


def fixed(capacity=MAX_BATCH):
    ticks = iter([1_000, 6_000] * 300)
    return Recorder(capacity, clock=lambda: next(ticks), wall=lambda: 1_700_000_000_123_000_000)


def test_structured_record_has_exact_units_and_no_caller_payload():
    recorder = fixed()
    with recorder.measure("worker.step"):
        pass
    (record,) = recorder.drain()
    assert record == Record("worker.step", 1_700_000_000_123, 5, "returned", "NONE")
    output = StringIO()
    write_json((record,), output)
    assert output.getvalue().endswith("\n") and len(output.getvalue()) < 512
    assert json.loads(output.getvalue()) == {
        "profile": "checkedflow/operation-observation/v1",
        "operation": "worker.step",
        "started_ms": 1_700_000_000_123,
        "duration_us": 5,
        "outcome": "returned",
        "reason": "NONE",
    }
    assert recorder.drain() == ()


@pytest.mark.parametrize(
    "error, outcome, reason",
    [
        (Failure("OUTCOME_UNKNOWN", "secret token and endpoint"), "failed", "OUTCOME_UNKNOWN"),
        (Failure("PRIVATE_CODE", "secret"), "failed", "OTHER"),
        (OSError("secret"), "failed", "OTHER"),
        (SystemExit("secret"), "interrupted", "OTHER"),
    ],
)
def test_errors_preserve_exact_exception_without_text(error, outcome, reason):
    recorder = fixed()
    with pytest.raises(type(error)) as caught, recorder.measure("effect.step"):
        raise error
    assert caught.value is error
    (record,) = recorder.drain()
    assert (record.outcome, record.reason) == (outcome, reason)
    assert "secret" not in record.json_line() and "PRIVATE_CODE" not in record.json_line()


def test_overflow_does_not_interrupt_work_or_replace_error():
    recorder = fixed(1)
    with recorder.measure("worker.step"):
        pass
    original = Failure("BUSY", "private")
    with pytest.raises(Failure) as caught, recorder.measure("effect.step"):
        raise original
    assert caught.value is original
    assert len(recorder.drain()) == 1


@pytest.mark.parametrize(
    "ticks, wall",
    [
        ([2, 1], 100),
        ([True, 3], 100),
        ([1, False], 100),
        ([1, 3], -1),
        ([1, 3], True),
        ([1, 3], 10**30),
    ],
)
def test_invalid_clock_omits_observation_without_affecting_work(ticks, wall):
    source = iter(ticks)
    recorder = Recorder(clock=lambda: next(source), wall=lambda: wall)
    with recorder.measure("worker.step"):
        result = 42
    assert result == 42 and recorder.drain() == ()


def test_unavailable_clock_at_entry_or_exit_never_masks_original_error():
    def unavailable():
        raise OSError("secret")

    for recorder in (
        Recorder(clock=unavailable),
        Recorder(wall=unavailable),
        Recorder(clock=iter([1]).__next__),
    ):
        with pytest.raises(ValueError, match="original"), recorder.measure("service.observe"):
            raise ValueError("original")
        assert recorder.drain() == ()


def test_queue_is_bounded_across_threads_and_drains_with_a_limit():
    recorder = Recorder(32)

    def observe(_):
        with recorder.measure("service.observe"):
            return 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(observe, range(100))) == 100
    assert len(recorder.drain(2)) == 2
    assert len(recorder.drain()) == 30
    assert recorder.drain() == ()
    for invalid in (0, 257, True):
        with pytest.raises(Failure):
            Recorder(invalid)
        with pytest.raises(Failure):
            recorder.drain(invalid)
    with pytest.raises(Failure), recorder.measure("https://secret.example"):
        pytest.fail("unknown operation must not enter")


def test_record_and_export_reject_unbounded_or_incoherent_input():
    record = Record("worker.step", 0, 0, "returned", "NONE")
    for changes in (
        {"operation": "private"},
        {"started_ms": -1},
        {"duration_us": True},
        {"outcome": "accepted"},
        {"reason": "private"},
        {"reason": "OTHER"},
    ):
        with pytest.raises(Failure):
            replace(record, **changes)
    with pytest.raises(Failure):
        write_json((record,) * 257, StringIO())
    with pytest.raises(Failure):
        trace((record,) * 257, None)

    class BrokenOutput:
        def write(self, _):
            raise OSError("export unavailable")

    with pytest.raises(OSError):
        write_json((record,), BrokenOutput())


def test_actual_otel_spans_are_explicit_roots_without_ambient_identity_or_exception():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("checkedflow-test")
    record = Record("effect.step", 1_700_000_000_000, 25, "failed", "OUTCOME_UNKNOWN")
    try:
        with tracer.start_as_current_span("untrusted-parent"):
            trace((record, replace(record, outcome="returned", reason="NONE")), tracer)
        spans = exporter.get_finished_spans()
        assert len(spans) == 3
        first, second = spans[:2]
        assert first.parent is None and second.parent is None
        assert first.start_time == record.started_ms * 1_000_000
        assert first.end_time - first.start_time == 25_000
        assert first.status.status_code == otel.StatusCode.ERROR
        assert second.status.status_code == otel.StatusCode.UNSET
        assert dict(first.attributes) == {
            "checkedflow.outcome": "failed",
            "checkedflow.reason": "OUTCOME_UNKNOWN",
            "checkedflow.duration_us": 25,
        }
        assert not first.events and not first.links
    finally:
        provider.shutdown()


def test_worker_and_effect_observations_do_not_reexecute_or_count_commits(tmp_path, monkeypatch):
    worker = Worker(tmp_path / "worker")
    worker.supervisor.recorder = Recorder()
    assert worker.supervisor.step(worker.node.task) == "finished"
    assert worker.supervisor.step(worker.node.task) == "finished"
    assert worker.calls == 1
    rows = worker.supervisor.recorder.drain()
    assert len(rows) == 2 and all(row.operation == "worker.step" for row in rows)
    executor = Executor(tmp_path / "effect", monkeypatch)
    executor.supervisor.recorder = Recorder()
    assert executor.step() == "observed" and executor.step() == "observed"
    assert executor.posts == 1
    rows = executor.supervisor.recorder.drain()
    assert len(rows) == 2 and all(row.operation == "effect.step" for row in rows)


def test_monitor_returned_record_does_not_claim_ready():
    _, monitor = fixture()
    monitor.recorder = Recorder()
    assert not monitor.observe().ready
    (record,) = monitor.recorder.drain()
    assert record.outcome == "returned" and record.operation == "service.observe"


def test_packaged_record_schema_matches_json_and_rejects_false_outcome():
    schema = json.loads(
        files("checkedflow").joinpath("data/operation-observation.schema.json").read_bytes()
    )
    validator = Draft202012Validator(schema)
    row = json.loads(Record("worker.step", 0, 0, "returned", "NONE").json_line())
    validator.validate(row)
    for invalid in (
        row | {"token": "private"},
        row | {"reason": "OTHER"},
        row | {"outcome": "failed"},
        row | {"duration_us": -1},
    ):
        assert not validator.is_valid(invalid)
