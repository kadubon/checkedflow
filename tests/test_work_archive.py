"""Retirement preserves liabilities, accounting, authenticated history and atomic recovery."""

import sqlite3
from dataclasses import replace
from io import BytesIO

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_work_acceptance import prepared
from test_work_tasks import Harness

from checkedflow.core.values import Failure
from checkedflow.core.work_acceptance import Candidate
from checkedflow.core.work_archive import Head, WorkArchive, retire
from checkedflow.core.work_budget import Ledger, Ticket
from checkedflow.core.work_tasks import Task
from checkedflow.operational_backup import export_history, restore_history
from checkedflow.operational_codec import decode_work_archive, work_archive_bytes
from checkedflow.operational_runtime import Runtime
from checkedflow.operational_storage import Store
from checkedflow.wire import document, dumps


def payload(tickets=(), tasks=(), candidates=(), root="0" * 64):
    return dict(
        mission="m",
        expected_root=root,
        tickets=list(tickets),
        tasks=list(tasks),
        candidates=list(candidates),
    )


def archive(ledger, tasks=(), candidates=(), **changes):
    arguments = dict(epoch=1, height=10, chain="test", mission="m", mode="paused")
    arguments.update(changes)
    selected = arguments.pop(
        "selected",
        payload(
            [t.identity for t in ledger.tickets],
            [t.identity for t in tasks],
            [c.identity for c in candidates],
        ),
    )
    return retire(Head(), ledger, tasks, candidates, selected, **arguments)


@given(st.integers(0, 100), st.integers(0, 100))
def test_retirement_preserves_independent_accounting(execute, verify):
    ledger = Ledger(
        300,
        (
            Ticket("0:a", "execute", 100, "a" * 64, "settled", execute),
            Ticket("0:b", "verify", 100, "b" * 64, "settled", verify),
        ),
        100,
    )
    head, after, tasks, candidates, batch = archive(ledger)
    assert after.spent == execute + verify == ledger.spent
    assert after.available == 300 - execute - verify
    assert after.protected_verification == 100 - verify
    assert not after.tickets and not tasks and not candidates
    assert head == Head(1, batch.root)
    assert decode_work_archive(work_archive_bytes(batch), batch.root) == batch
    assert dumps(document(batch.body)) == batch.body


@pytest.mark.parametrize("status", ["reserved", "unknown"])
def test_unknown_or_reserved_funding_is_pinned(status):
    ledger = Ledger(
        10, (Ticket("0:a", "verify", 10, "a" * 64, status, 10 if status == "unknown" else 0),), 1
    )
    with pytest.raises(Failure, match="reserved or uncertain"):
        archive(ledger)


@pytest.mark.parametrize("status", ["ready", "leased", "running", "unknown"])
def test_unfinished_or_unknown_tasks_are_pinned(status):
    ledger = Ledger(10, (Ticket("0:a", "verify", 10, "a" * 64, "settled", 10),), 1)
    task = Task("0:t", "0:a", ("v",), 5, 100, 1, status=status)
    with pytest.raises(Failure, match="unfinished or uncertain task"):
        archive(ledger, (task,))


def test_dependencies_expiry_and_revocation():
    ledger = Ledger(10, (Ticket("0:a", "verify", 10, "a" * 64, "settled", 10),), 1)
    task = Task("0:t", "0:a", ("v",), 5, 100, 1, status="finished")
    candidate = Candidate("0:c", "a" * 64, "b" * 64, 1, 20, ("0:t",))
    with pytest.raises(Failure, match="live candidate"):
        archive(ledger, (task,), (candidate,))
    with pytest.raises(Failure, match="pins checks"):
        archive(ledger, (task,), (candidate,), selected=payload(tasks=["0:t"]))
    with pytest.raises(Failure, match="pins funding"):
        archive(ledger, (task,), selected=payload(tickets=["0:a"]))
    with pytest.raises(Failure, match="uncertain checks"):
        archive(ledger, (replace(task, status="unknown"),), (replace(candidate, revoked=True),))
    assert not archive(ledger, (task,), (candidate,), height=20)[3]
    assert not archive(ledger, (task,), (replace(candidate, revoked=True),))[3]
    assert archive(ledger, (task,), (candidate,), selected=payload(candidates=["0:c"]), height=20)[
        2
    ] == (task,)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"mode": "running"}, "pause"),
        ({"selected": payload(root="f" * 64)}, "predecessor"),
        ({"selected": payload()}, "nonempty"),
        ({"selected": payload(tickets=["0:missing"])}, "missing"),
        ({"epoch": 0}, "request epoch"),
    ],
)
def test_retirement_preconditions(changes, message):
    ledger = Ledger(10, (Ticket("0:a", "verify", 10, "a" * 64, "released"),), 1)
    with pytest.raises(Failure, match=message):
        archive(ledger, **changes)


def scenario():
    h = prepared()
    for index in range(4):
        h.observation(index)
    h.send("mission.pause", {})
    h.send("artifact.revoke", {"candidate": h.candidate})
    h.send("journal.rollover", {})
    before = h.runtime.state
    _, raw = h.send(
        "history.archive",
        payload([t.identity for t in before.budget.tickets], h.checks, [h.candidate]),
    )
    h.runtime.apply(raw, height=h.runtime.state.height)
    assert h.runtime.state.budget.spent == 40
    assert not h.runtime.state.tasks and not h.runtime.state.candidates
    return h


def persist(tmp_path, h):
    store = Store(tmp_path / "state.sqlite", h.initial)
    previous = Runtime(h.initial).state_hash
    for height, raw in h.events:
        result = store.commit_block(height, [raw], previous_hash=previous)
        assert result.outcomes == ("OK",)
        previous = result.state_hash
    return store


def test_signed_history_backup_and_reopen(tmp_path):
    h = scenario()
    store = persist(tmp_path, h)
    assert Store(store.path, h.initial).load() == h.runtime.state
    assert store.verify_history(expected_hash=h.runtime.state_hash) == h.runtime.state
    batch = store.work_archive(1, expected_root=h.runtime.state.history.root)
    assert len(document(batch.body)["candidates"]) == 1
    with pytest.raises(Failure, match="commitment"):
        store.work_archive(1, expected_root="f" * 64)
    with pytest.raises(Failure, match="unavailable"):
        store.work_archive(2, expected_root=batch.root)
    output = BytesIO()
    checkpoint = export_history(store, output, expected_hash=h.runtime.state_hash)
    restored = restore_history(
        BytesIO(output.getvalue()),
        tmp_path / "restored",
        initial=h.initial,
        checkpoint=checkpoint,
        current_height=checkpoint.height,
    )
    assert restored.load() == h.runtime.state
    assert restored.work_archive(1, expected_root=batch.root) == batch


def test_archive_write_failure_rolls_back_head_and_block(tmp_path):
    h = scenario()
    last = h.events.pop()
    store = persist(tmp_path, h)
    before = store.load()
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER reject_archive BEFORE INSERT ON work_archives "
            "BEGIN SELECT RAISE(ABORT, 'fixture rejection'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="fixture rejection"):
        store.commit_block(last[0], [last[1]], previous_hash=Runtime(before).state_hash)
    assert store.load() == before
    assert store.verify_history(expected_hash=Runtime(before).state_hash) == before


def test_old_sqlite_profile_is_upgraded_without_state_change(tmp_path):
    h = Harness()
    store = Store(tmp_path / "old.sqlite", h.initial)
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE work_archives")
    assert Store(store.path, h.initial).load() == h.initial


def test_archive_codec_rejects_bad_commitments_sizes_and_noncanonical_bytes():
    ledger = Ledger(10, (Ticket("0:a", "verify", 10, "a" * 64, "released"),), 1)
    batch = archive(ledger)[4]
    with pytest.raises(Failure):
        Head(0, "a" * 64)
    with pytest.raises(Failure):
        _ = WorkArchive("0" * 64, 1, b"x" * 1048577).root
    with pytest.raises(Failure):
        decode_work_archive(b"x" * 1048833, batch.root)
    with pytest.raises(Failure, match="noncanonical"):
        decode_work_archive(b" " + work_archive_bytes(batch), batch.root)
    with pytest.raises(Failure, match="record epoch"):
        archive(replace(ledger, tickets=(replace(ledger.tickets[0], identity="bad"),)))


def test_more_than_4096_retired_records_keep_cumulative_cost_and_bounded_state():
    head, ledger = Head(), Ledger(5000, verification_reserve=4500)
    total = 0
    for epoch in range(36):
        tickets = tuple(
            Ticket(f"{epoch}:{i:03}", "verify", 1, "a" * 64, "settled", 1) for i in range(128)
        )
        ledger = replace(ledger, tickets=tickets)
        selected = payload([t.identity for t in tickets], root=head.root)
        previous = head.root
        head, ledger, tasks, candidates, batch = retire(
            head,
            ledger,
            (),
            (),
            selected,
            epoch=epoch + 1,
            height=epoch + 1,
            chain="test",
            mission="m",
            mode="paused",
        )
        total += 128
        assert batch.previous_root == previous and head.sequence == epoch + 1
        assert ledger.spent == total and ledger.available == 5000 - total
        assert ledger.protected_verification == max(0, 4500 - total)
        assert not ledger.tickets and not tasks and not candidates
    assert total == 4608


@pytest.mark.parametrize("tamper", ["missing", "body", "sequence", "scope"])
def test_current_work_archive_corruption_stops_use(tmp_path, tamper):
    h = scenario()
    store = persist(tmp_path, h)
    with sqlite3.connect(store.path) as db:
        if tamper == "missing":
            db.execute("DELETE FROM work_archives")
        elif tamper == "body":
            db.execute("UPDATE work_archives SET body=?", (b"{}",))
        else:
            original = store.work_archive(1, expected_root=h.runtime.state.history.root)
            body = document(original.body)
            if tamper == "scope":
                body["mission"] = "foreign"
            batch = replace(original, sequence=2 if tamper == "sequence" else 1, body=dumps(body))
            from hashlib import sha256

            from checkedflow.operational_codec import state_bytes

            state = replace(h.runtime.state, history=Head(1, batch.root))
            raw = state_bytes(state)
            db.execute("UPDATE head SET body=?, hash=?", (raw, sha256(raw).hexdigest()))
            db.execute(
                "UPDATE work_archives SET body=?, root=?", (work_archive_bytes(batch), batch.root)
            )
    with pytest.raises(Failure):
        store.load()


def test_portable_schemas_and_nonzero_extension_invariants():
    import json
    from importlib.resources import files

    from jsonschema import Draft202012Validator

    from checkedflow.operational_codec import decode, encode

    h = scenario()
    state = encode(h.runtime.state)
    resources = files("checkedflow").joinpath("data")
    for filename, value in [
        ("operational-state.schema.json", state),
        ("work-archive-command.schema.json", document(h.events[-1][1])["command"]),
    ]:
        schema = json.loads(resources.joinpath(filename).read_text())
        if filename == "work-archive-command.schema.json":
            value = {"kind": value["kind"], "payload": value["payload"]}
        Draft202012Validator(schema).validate(value)
    del state["history"]
    with pytest.raises(Failure, match="without archive"):
        decode(dumps(state))


def test_signed_retirement_denies_current_epoch_and_unprivileged_actor():
    h = Harness()
    task, ticket = h.prepare()
    h.send("mission.pause", {})
    h.send("task.cancel", {"task": task})
    selected = payload([ticket], [task])
    before = h.runtime.state
    with pytest.raises(Failure, match="request epoch"):
        h.send("history.archive", selected)
    assert h.runtime.state == before
    h.send("journal.rollover", {})
    with pytest.raises(Failure):
        h.send("history.archive", selected, "worker")
    h.send("history.archive", selected)
    assert h.runtime.state.budget.spent == 0 and h.runtime.state.history.sequence == 1


def test_packaged_signed_vector_authenticates_retired_rows():
    import json
    from importlib.resources import files

    from jsonschema import Draft202012Validator

    from checkedflow.operational_codec import decode

    resources = files("checkedflow").joinpath("data")
    vector = json.loads(resources.joinpath("work-archive-vector.json").read_text())
    schema = json.loads(resources.joinpath("work-archive.schema.json").read_text())
    runtime = Runtime(decode(dumps(vector["initial"])))
    batches = []
    for event in vector["events"]:
        batch = runtime.apply(dumps(event["envelope"]), height=event["height"])
        if isinstance(batch, WorkArchive):
            value = document(work_archive_bytes(batch))
            Draft202012Validator(schema).validate(value)
            batches.append({"root": batch.root, "record": value})
    assert runtime.state_hash == vector["state_hash"]
    assert batches == vector["archives"]
