"""Residual preservation, causal repair binding and verifier disagreement regressions."""

import pytest

from checkedflow.core.values import Failure


def test_heartbeat_extends_without_recharging_then_abandon(h):
    h.task(finish=False)
    before = h.runtime.state
    state = h.apply("task.heartbeat", {"id": "t", "fence": 1})
    assert state.tasks["t"].deadline > before.tasks["t"].deadline
    assert state.missions["m"].reserved == before.missions["m"].reserved
    h.runtime.tick(state.tasks["t"].deadline)
    state = h.apply(
        "task.reconcile",
        {"id": "t", "retry": False, "reason": "external outcome unknown"},
        admin=True,
    )
    assert state.tasks["t"].status == "abandoned"
    assert state.residuals["timeout:t:1"].status == "open"
    assert state.missions["m"].spent == 2


def test_disagreement_is_committed_evidence_and_blocks_reuse(h):
    h.task()
    h.propose()
    h.vote(index=0, behavior="a" * 64)
    state = h.vote(index=1, behavior="b" * 64)
    assert len(state.capabilities["c"].votes) == 2
    assert state.capabilities["c"].status == "quarantined"
    assert state.residuals["invalid:c:c"].reason == "verification behavior mismatch"
    assert state.missions["m"].spent == 4
    assert state.missions["m"].reserved == 0


def test_unknown_vote_retry_releases_unfunded_abandoned_work(h):
    h.task()
    h.propose()
    h.vote(outcome="unknown")
    h.apply(
        "task.reconcile",
        {"id": "verify:c:org0", "retry": True, "reason": "recheck approved"},
        admin=True,
    )
    state = h.apply("capability.revoke", {"id": "c", "reason": "withdrawn"}, admin=True)
    assert state.tasks["verify:c:org0"].status == "abandoned"
    assert state.missions["m"].reserved == 0
    repeated = h.apply("capability.revoke", {"id": "c", "reason": "same incident"}, admin=True)
    assert repeated.residuals["invalid:c:c"] == state.residuals["invalid:c:c"]


def test_resolution_requires_scoped_checked_repair_and_keeps_history(h):
    h.task("unknown", finish=False)
    h.apply(
        "task.finish",
        {"id": "unknown", "fence": 1, "result": {"outcome": "unknown", "evidence": {}}},
    )
    rid = "result:unknown:1"
    h.checked()
    with pytest.raises(Failure, match="EVIDENCE"):
        h.apply("residual.resolve", {"id": rid, "evidence": "c", "reason": "unrelated"}, admin=True)
    h.checked("repair", "repair-task", spec={"target": ["double"], "repairs": [rid]})
    before = h.runtime.state.residuals[rid]
    state = h.apply(
        "residual.resolve",
        {"id": rid, "evidence": "repair", "reason": "scoped repair checked"},
        admin=True,
    )
    assert state.residuals[rid].status == "resolved"
    assert state.residuals[rid].reason == before.reason
    assert state.residuals[rid].resolution == "repair"
    with pytest.raises(Failure, match="STATE"):
        h.apply(
            "residual.resolve", {"id": rid, "evidence": "repair", "reason": "duplicate"}, admin=True
        )
    reopened = h.apply(
        "capability.revoke", {"id": "repair", "reason": "repair evidence withdrawn"}, admin=True
    )
    assert reopened.residuals[rid].status == "open"
    assert reopened.residuals[rid].resolution == "repair"
    assert reopened.residuals[rid].reason == before.reason


def test_diamond_invalidation_has_no_double_refund(h):
    h.checked("root", "root-task")
    h.checked("left", "left-task", dependencies=("root",))
    h.checked("right", "right-task", dependencies=("root",))
    h.checked("leaf", "leaf-task", dependencies=("left", "right"))
    state = h.apply("capability.revoke", {"id": "root", "reason": "upstream invalid"}, admin=True)
    assert state.missions["m"].reserved == 0
    assert all(c.status != "checked" for c in state.capabilities.values())
    assert len([r for r in state.residuals.values() if r.subject == "leaf"]) == 1


def test_revocation_of_worker_without_votes_does_not_withdraw_capability(h):
    h.checked()
    state = h.apply("worker.revoke", {"id": "w3", "reason": "retired"}, admin=True)
    assert state.capabilities["c"].status == "checked"
