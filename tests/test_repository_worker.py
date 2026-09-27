"""Repository worker binding and verified evidence; real execution lives in qualification."""

from dataclasses import replace

import pytest
from patch_fixture import invoice
from test_worker_submission import Node

from checkedflow.artifact_io import Access
from checkedflow.artifacts import LocalStore
from checkedflow.core.values import Failure
from checkedflow.repository_execution import Observation
from checkedflow.repository_reuse import contract_digest
from checkedflow.repository_worker import EvidencePublisher, RepositoryExecutor
from checkedflow.wire import document


@pytest.mark.parametrize("matched", [True, False, None])
def test_repository_observation_preserves_checker_unknown(monkeypatch, matched):
    base, patch, contract, cases = invoice("python@sha256:" + "a" * 64)
    executor = RepositoryExecutor(base, patch, contract, cases)
    node = Node()
    state = node.read()
    ticket = replace(state.budget.tickets[0], target=contract_digest(contract))
    state = replace(state, budget=replace(state.budget, tickets=(ticket,)))
    observation = Observation(
        ticket.target,
        state.height,
        contract.result_tree,
        contract.test_inventory_digest,
        contract.checker_digest,
        contract.image,
        "b" * 64,
        "c" * 64,
        matched,
        "fixture",
    )
    monkeypatch.setattr("checkedflow.repository_worker.observe_patch", lambda *a, **k: observation)
    result = executor(state, state.tasks[0])
    assert result.outcome == ("unknown" if matched is None else "reported")
    assert document(result.evidence)["case_match"] is matched
    with pytest.raises(Failure, match="contract differs"):
        executor(node.read(), node.read().tasks[0])
    state = replace(
        state, budget=replace(state.budget, tickets=(replace(ticket, phase="generate"),))
    )
    with pytest.raises(Failure, match="observation phase"):
        executor(state, state.tasks[0])


def test_publisher_requires_scoped_verified_readback(tmp_path):
    node = Node()
    access = Access("worker", frozenset({"m"}), frozenset({"read", "write"}))
    store = LocalStore(tmp_path / "evidence.sqlite")
    publish = EvidencePublisher(store, access)
    fingerprint = publish(node.read(), node.read().tasks[0], b"{}")
    assert len(fingerprint) == 64
    with pytest.raises(Failure, match="AUTHORITY"):
        EvidencePublisher(store, replace(access, scopes=frozenset()))(
            node.read(), node.read().tasks[0], b"{}"
        )

    class BadStore:
        def put(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return b"wrong"

    with pytest.raises(Failure, match="readback"):
        EvidencePublisher(BadStore(), access)(node.read(), node.read().tasks[0], b"{}")
