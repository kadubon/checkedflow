"""Original-byte nonce coordination under lost replies, contention and epoch retirement."""

import sqlite3
from dataclasses import replace

import pytest
from test_work_tasks import Harness

from checkedflow.core.values import Failure
from checkedflow.operational_codec import archive_bytes
from checkedflow.wire import document
from checkedflow.worker_submission import Coordinator


class Node:
    def __init__(self):
        self.h = Harness()
        self.task, _ = self.h.prepare(lease_blocks=100)
        self.sent = []
        self.behavior = "normal"

    def read(self):
        return self.h.runtime.state

    def submit(self, raw):
        self.sent.append(raw)
        if self.behavior == "before":
            raise OSError("fixture disconnected before commit")
        if self.behavior == "empty":
            return {}
        self.h.runtime.apply(raw, height=self.read().height + 1)
        if self.behavior == "after":
            raise OSError("fixture reply lost after commit")
        return {}

    def coordinator(self, path, **changes):
        options = dict(chain=self.h.initial.chain, mission="m", actor="worker", revision=1)
        options.update(changes)
        return Coordinator(path, self.read, self.submit, self.h.keys[("worker", 1)], **options)

    def lease(self):
        return {"mission": "m", "task": self.task}


def test_durable_nonce_sequence_and_completed_request_repetition(tmp_path):
    node = Node()
    c = node.coordinator(tmp_path)
    assert c.reconcile() == "idle" and c.pending() is None
    state = c.send("0:lease", "task.lease", node.lease())
    assert state.tasks[0].owner == "worker" and c.pending() is None
    assert c.send("0:lease", "task.lease", node.lease()) == state
    assert len(node.sent) == 1
    with pytest.raises(Failure, match="arguments differ"):
        c.send("0:lease", "task.start", node.lease() | {"fence": 1})
    c = node.coordinator(tmp_path)
    c.send("0:start", "task.start", node.lease() | {"fence": 1})
    c.send("0:heartbeat", "task.heartbeat", node.lease() | {"fence": 1})
    state = c.send(
        "0:finish",
        "task.finish",
        node.lease() | {"fence": 1, "outcome": "reported", "evidence": "c" * 64},
    )
    assert state.tasks[0].status == "finished"
    assert [document(raw)["command"]["nonce"] for raw in node.sent] == [1, 2, 3, 4]


@pytest.mark.parametrize("retirement", ["epoch", "credential"])
def test_cached_confirmation_requires_current_admission(tmp_path, retirement):
    node = Node()
    c = node.coordinator(tmp_path)
    c.send("0:lease", "task.lease", node.lease())
    if retirement == "epoch":
        node.h.send("journal.rollover", {})
        code = "RETIRED_REQUEST"
    else:
        node.h.send("key.revoke", {"identity": "worker", "revision": 1, "reason": "compromise"})
        code = "SIGNATURE"
    with pytest.raises(Failure, match=code):
        node.coordinator(tmp_path).send("0:lease", "task.lease", node.lease())
    assert len(node.sent) == 1 and c.pending() is None


@pytest.mark.parametrize("behavior", ["before", "after", "empty"])
def test_lost_or_unproven_replies_preserve_original_intent(tmp_path, behavior):
    node = Node()
    node.behavior = behavior
    c = node.coordinator(tmp_path)
    with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
        c.send("0:lease", "task.lease", node.lease())
    raw = c.pending()
    assert raw == node.sent[0]
    c = node.coordinator(tmp_path)
    with pytest.raises(Failure, match="outstanding"):
        c.send("0:new", "task.lease", node.lease())
    if behavior == "after":
        assert c.send("0:lease", "task.lease", node.lease()).tasks[0].status == "leased"
    else:
        assert c.reconcile() == "pending"
        with pytest.raises(Failure, match="uncertain"):
            c.send("0:lease", "task.lease", node.lease())
        node.behavior = "normal"
        c.retransmit()
        assert node.sent == [raw, raw]
    assert c.pending() is None


def test_retransmission_is_explicit_finite_and_never_resigns(tmp_path):
    node = Node()
    node.behavior = "before"
    c = node.coordinator(tmp_path)
    with pytest.raises(Failure):
        c.send("0:lease", "task.lease", node.lease())
    for _ in range(2):
        with pytest.raises(Failure, match="OUTCOME_UNKNOWN"):
            c.retransmit()
    with pytest.raises(Failure, match="transmission limit"):
        node.coordinator(tmp_path).retransmit()
    assert len(node.sent) == 3 and len(set(node.sent)) == 1
    node.h.runtime.apply(node.sent[0], height=node.read().height + 1)
    assert c.retransmit().tasks[0].status == "leased"
    assert len(node.sent) == 3
    with pytest.raises(Failure, match="no pending"):
        c.retransmit()


@pytest.mark.parametrize("committed", [True, False])
def test_anchored_retired_epoch_confirms_membership_or_definitive_absence(tmp_path, committed):
    node = Node()
    node.behavior = "after" if committed else "before"
    c = node.coordinator(tmp_path)
    with pytest.raises(Failure):
        c.send("0:lease", "task.lease", node.lease())
    node.h.send("journal.rollover", {})
    # Replay the rollover against its predecessor to obtain the authentic archive.
    from checkedflow.operational_runtime import Runtime

    runtime = Runtime(node.h.initial)
    for height, raw in node.h.events[:-1]:
        runtime.apply(raw, height=height)
    if committed:
        runtime.apply(node.sent[0], height=runtime.state.height + 1)
    batch = runtime.apply(node.h.events[-1][1], height=node.h.events[-1][0])
    assert batch.root == node.read().journal.archive_root
    assert c.reconcile() == "pending"
    with pytest.raises(Failure):
        c.reconcile(archive=b"{}")
    assert c.pending() is not None
    result = c.reconcile(archive=archive_bytes(batch))
    assert result == ("confirmed" if committed else "retired_absent")
    assert c.pending() is None
    if not committed:
        with pytest.raises(Failure, match="RETIRED_REQUEST"):
            c.send("0:lease", "task.lease", node.lease())


def test_scope_purpose_signer_and_preflight_denials_precede_send(tmp_path):
    node = Node()
    c = node.coordinator(tmp_path)
    with pytest.raises(Failure, match="another identity"):
        node.coordinator(tmp_path, actor="other")
    with pytest.raises(Failure, match="only worker"):
        c.send("0:admin", "mission.pause", {"mission": "m"})
    with pytest.raises(Failure, match="mission differs"):
        c.send("0:lease", "task.lease", node.lease() | {"mission": "foreign"})
    with pytest.raises(Failure):
        c.send(
            "0:finish",
            "task.finish",
            node.lease() | {"fence": 1, "outcome": "reported", "evidence": "c" * 64},
        )
    wrong = Coordinator(
        tmp_path,
        node.read,
        node.submit,
        node.h.keys[("other", 1)],
        chain=node.h.initial.chain,
        mission="m",
        actor="worker",
        revision=1,
    )
    with pytest.raises(Failure, match="SIGNATURE"):
        wrong.send("0:lease", "task.lease", node.lease())
    assert c.pending() is None and not node.sent


def test_cross_process_lock_excludes_all_concurrent_submission(tmp_path):
    node = Node()
    c = node.coordinator(tmp_path)
    with sqlite3.connect(tmp_path / "submission-lock.sqlite") as lock:
        lock.execute("BEGIN IMMEDIATE")
        with pytest.raises(Failure, match="BUSY"):
            c.send("0:lease", "task.lease", node.lease())
    assert not node.sent
    c.send("0:lease", "task.lease", node.lease())


@pytest.mark.parametrize(
    "change, code",
    [
        (lambda s: replace(s, chain="foreign"), "SCOPE"),
        (lambda s: replace(s, profile="future"), "VERSION"),
        (
            lambda s: replace(
                s,
                journal=replace(
                    s.journal,
                    actors=tuple(pair for pair in s.journal.actors if pair[0] != "worker"),
                ),
            ),
            "AUTHORITY",
        ),
        (lambda s: replace(s, height=s.height - 1), "STALE"),
        (lambda s: replace(s, mode="paused"), "CONFLICT"),
    ],
)
def test_invalid_own_node_observations_never_clear_pending(tmp_path, change, code):
    node = Node()
    node.behavior = "before"
    c = node.coordinator(tmp_path)
    with pytest.raises(Failure):
        c.send("0:lease", "task.lease", node.lease())
    snapshot = node.read()
    c._read = lambda: change(snapshot)
    with pytest.raises(Failure, match=code):
        c.reconcile()
    assert c.pending() == node.sent[0]


def test_conflicting_committed_request_stays_unresolved(tmp_path):
    node = Node()
    node.behavior = "before"
    c = node.coordinator(tmp_path)
    with pytest.raises(Failure):
        c.send("0:lease", "task.lease", node.lease())
    node.h.runtime.apply(node.sent[0], height=node.read().height + 1)
    state = node.read()
    receipts = tuple(
        replace(r, command_digest="f" * 64) if r.request == "0:lease" else r
        for r in state.journal.receipts
    )
    c._read = lambda: replace(state, journal=replace(state.journal, receipts=receipts))
    with pytest.raises(Failure, match="receipt differs"):
        c.reconcile()
    assert c.pending() is not None


@pytest.mark.parametrize("committed", [False, True])
def test_process_exit_before_or_after_remote_commit_keeps_signed_bytes(tmp_path, committed):
    import subprocess
    import sys
    from pathlib import Path

    from checkedflow.operational_codec import decode
    from checkedflow.operational_runtime import Runtime

    child = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from test_worker_submission import Node
from checkedflow.operational_codec import state_bytes
root = Path(sys.argv[2])
node = Node()
def submit(raw):
    (root / "original").write_bytes(raw)
    if sys.argv[3] == "True":
        node.submit(raw)
    (root / "state").write_bytes(state_bytes(node.read()))
    os._exit(39)
c = node.coordinator(root / "journal")
c._submit = submit
c.send("0:lease", "task.lease", node.lease())
"""
    result = subprocess.run(
        [sys.executable, "-c", child, str(Path(__file__).parent), str(tmp_path), str(committed)],
        timeout=30,
        check=False,
    )
    assert result.returncode == 39
    node = Node()
    node.h.runtime = Runtime(decode((tmp_path / "state").read_bytes()))
    coordinator = node.coordinator(tmp_path / "journal")
    original = (tmp_path / "original").read_bytes()
    assert coordinator.pending() == original
    if committed:
        assert coordinator.reconcile() == "confirmed" and not node.sent
    else:
        assert coordinator.reconcile() == "pending"
        coordinator.retransmit()
        assert node.sent == [original]


def test_other_coordinator_cannot_enter_during_network_submission(tmp_path):
    node = Node()
    first, second = node.coordinator(tmp_path), node.coordinator(tmp_path)

    def submit(raw):
        with pytest.raises(Failure, match="BUSY"):
            second.send("0:concurrent", "task.lease", node.lease())
        return node.submit(raw)

    first._submit = submit
    first.send("0:lease", "task.lease", node.lease())
    assert len(node.sent) == 1


def test_origin_pins_identity_roots_even_when_chain_name_and_height_look_valid(tmp_path):
    from checkedflow.operational_runtime import Runtime

    node = Node()
    c = node.coordinator(tmp_path)
    assert c.origin() == Runtime(node.h.initial).state_hash
    state = node.read()
    changed = (replace(state.credentials[0], public_key="f" * 64), *state.credentials[1:])
    c._read = lambda: replace(state, height=state.height + 1, credentials=changed)
    with pytest.raises(Failure, match="GENESIS"):
        c.observe()
    assert not node.sent


def test_application_key_rotation_preserves_coordinator_origin(tmp_path):
    node = Node()
    c = node.coordinator(tmp_path)
    origin = c.origin()
    state = node.read()
    worker = next(item for item in state.credentials if item.identity == "worker")
    credentials = tuple(
        replace(item, retired_height=state.height + 1) if item == worker else item
        for item in state.credentials
    )
    successor = replace(worker, revision=2, activated_height=state.height + 1, public_key="f" * 64)
    c._read = lambda: replace(
        state,
        height=state.height + 2,
        credentials=tuple(
            sorted((*credentials, successor), key=lambda item: (item.identity, item.revision))
        ),
    )
    assert c.origin() == origin


def test_older_local_journal_requires_explicit_migration(tmp_path):
    node = Node()
    node.coordinator(tmp_path)
    with sqlite3.connect(tmp_path / "submission.sqlite") as db:
        db.execute("ALTER TABLE coordinator DROP COLUMN origin")
    with pytest.raises(Failure, match="VERSION"):
        node.coordinator(tmp_path)
