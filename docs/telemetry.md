# Local operation logs and optional traces

Status: development SDK instrumentation for worker steps, effect steps and service observations.
This is not a complete deployment logging service, audit journal or achieved SLO.

## What a record means

A `Recorder` observes a local call. `returned` means the Python call returned; it does not mean
that a task passed verification, an effect committed, or a service was ready. `failed` means an
exception escaped. `interrupted` preserves process-level interruption such as `SystemExit`.
Original return values and exceptions remain unchanged, including when the queue is full.

A restarted supervisor may report two local step observations while executing a provider only
once. These records are not authoritative event counters. Never add them to modeled budget,
use them as execution evidence, or replay them to authorize work. Consensus state and the
protected recovery journals remain authoritative. Buffer loss is expected on crash and overflow;
this facility is not a tamper-evident audit trail or a complete error-count denominator.

## Opt in at the service boundary

Create one `checkedflow.telemetry.Recorder` and pass it as `recorder=` to the worker supervisor,
effect supervisor or `observability.Monitor`. Omission disables recording. No exporter, file,
listener, global tracer provider or background thread is created by the recorder.

Each queue holds at most 256 records; a configured smaller capacity is supported. An operation
adds a fixed-size record without exporter or storage I/O. When full, it drops the new observation
while preserving existing entries. `drain(limit)` removes at most the declared number of records,
between 1 and 256. Concurrent producers share this bound. There is no automatic delivery retry.

```python
from io import StringIO
from checkedflow.telemetry import Recorder, write_json

recorder = Recorder(capacity=64)
with recorder.measure("worker.step"):
    pass  # A trusted caller may instrument an existing step, never candidate code.

batch = recorder.drain(limit=32)
output = StringIO()
write_json(batch, output)
print(output.getvalue(), end="")  # Secret-free example only.
```

The actual supervisors already instrument `step`; do not wrap them a second time. The example
shows the record contract without executing work or granting authority. The other fixed operation
names are `effect.step` and `service.observe`. Arbitrary names are rejected.

## Portable record and information boundaries

`checkedflow schema operation-observation` returns the packaged JSON Schema. Each UTF-8 JSON
line contains exactly the profile, operation, start time, duration, outcome and bounded reason.
Start time uses Unix milliseconds from the local wall clock. Duration uses elapsed monotonic
microseconds. Neither affects consensus time, lease expiry or execution permission. Invalid,
backward or unavailable measurement clocks omit the observation, rather than inventing a duration.

Reasons use a fixed vocabulary of operational failures, plus `NONE` and `OTHER`. Raw exception
messages and stacks, task/mission identifiers, content digests, paths, URLs, headers, candidate
output and credentials are not recorded. Unknown failure codes become `OTHER`. Exported records
still describe operational activity, so access and retention belong to the operator.

`write_json(batch, output)` writes only an explicitly drained batch to an operator-supplied stream.
Export failures propagate to that caller. Provision file ownership, rotation, retention and disk
quotas independently. Do not call an unbounded sink from a consensus callback or a protected-work
step. The current SDK does not provision a log collector or promise lossless delivery.

## Optional OpenTelemetry spans

Install `checkedflow[telemetry]` to use the API adapter. It accepts an explicitly supplied tracer;
it does not install/configure the SDK or infer exporters from environment variables. The operator
owns the SDK provider, exporter destinations, transport credentials and service resource metadata.
Follow the [OpenTelemetry Python instrumentation guide](https://opentelemetry.io/docs/languages/python/instrumentation/).

```python
from checkedflow.telemetry import trace
from opentelemetry.trace import NoOpTracerProvider

tracer = NoOpTracerProvider().get_tracer("checkedflow-operator")
trace(batch, tracer)  # No exporter and no outbound telemetry in this example.
```

The adapter creates a root span for each observed interval. Ambient trace context, including
client-supplied parents, is deliberately not inherited. It does not set a global/current span or
copy exception text. Only the fixed outcome, reason and measured duration become attributes.
Error/interruption sets error status; `returned` leaves status unset instead of claiming acceptance.
Wall-clock start is rounded to milliseconds and the end is reconstructed from monotonic duration.
Spans are diagnostic correlation, never authorization or signed evidence.

Call exporters from a separately supervised operator process with finite batch count, CPU/memory,
network deadlines and shutdown limits. A batch is bounded to 256 spans; the adapter cannot enforce
an arbitrary third-party exporter's I/O behavior. Complete collector provisioning, explicit
cross-process delivery and timeout qualification remain deployment work. Do not claim this SDK
alone satisfies the specification's bounded exporter deployment requirement.

## Verification and remaining scope

Tests exercise overflow, concurrent producers, invalid clocks, original exception identity,
secret-free JSON, schema rejection and actual OpenTelemetry SDK in-memory spans. They verify
that ambient parents are absent and that repeated supervisor observations do not reexecute work.
The real four-node/gVisor effect case additionally checks the failed/returned observation pair
around a deliberately lost report reply; that extension requires its own installed qualification.

Protocol/callback/signer/backup error instrumentation, protected correlation identifiers, measured
SLIs, collector lifecycle, dashboard/alert provisioning and long-duration load/recovery evidence
remain unfinished. Use the implementation ledger, not the presence of a telemetry API, to assess
release readiness.
