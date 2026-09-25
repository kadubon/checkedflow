"""Inject crashes at the receipt/proposal boundary without rerunning the generator."""

import pytest

from checkedflow.core.values import Failure
from checkedflow.worker import Worker


class LocalClient:
    def __init__(self, harness, *, lost_reply=""):
        self.harness = harness
        self.lost_reply = lost_reply

    def state(self):
        return self.harness.runtime.state

    def submit(self, envelope):
        self.harness.runtime.apply(envelope, height=self.state().height + 1)
        if envelope["command"]["kind"] == self.lost_reply:
            self.lost_reply = ""
            raise OSError("committed response was lost")
        return {}


def make_worker(h, client):
    return Worker(client, "w0", h.keys["w0"], h.initial.chain)


def test_lost_finish_response_resumes_exact_candidate_once(h, monkeypatch):
    h.task(start=False)
    client = LocalClient(h, lost_reply="task.finish")
    with pytest.raises(OSError, match="lost"):
        make_worker(h, client).once()
    receipt = h.runtime.state.tasks["t"].result["evidence"]
    assert receipt["source"] and len(h.runtime.state.capabilities) == 0
    assert h.runtime.state.missions["m"].spent == 2

    def forbidden(*_args):
        pytest.fail("a committed generation receipt must not rerun code")

    monkeypatch.setattr(Worker, "execute", forbidden)
    result = make_worker(h, client).once()
    assert result == {"task": "t", "status": "proposed", "resumed": True}
    cap = next(iter(h.runtime.state.capabilities.values()))
    assert cap.source == receipt["source"]
    assert h.runtime.state.missions["m"].spent == 2
    assert h.runtime.state.missions["m"].reserved == 4
    # Repeated metadata admission is rejected, without a second reservation.
    with pytest.raises(Failure, match="CONFLICT"):
        make_worker(h, client).propose(h.runtime.state, "t", h.runtime.state.tasks["t"])
    assert h.runtime.state.missions["m"].reserved == 4


def test_started_attempt_is_never_automatically_reexecuted(h, monkeypatch):
    h.task(finish=False, effect="external")
    monkeypatch.setattr(Worker, "execute", lambda *_: pytest.fail("unsafe replay"))
    assert make_worker(h, LocalClient(h)).once() == {"status": "idle"}
    h.runtime.tick(h.runtime.state.tasks["t"].deadline)
    assert make_worker(h, LocalClient(h)).once() == {"status": "idle"}
    assert h.runtime.state.tasks["t"].status == "uncertain"


def test_malformed_adapter_spec_becomes_charged_uncertainty(h):
    h.task(start=False, spec={"missing_target": True})
    result = make_worker(h, LocalClient(h)).once()
    assert result["outcome"] == "unknown"
    state = h.runtime.state
    assert state.tasks["t"].status == "uncertain"
    assert state.missions["m"].spent == 2 and state.missions["m"].reserved == 0
    assert state.residuals["result:t:1"].status == "open"


def test_executor_without_producer_role_does_not_acquire_generation(h):
    from checkedflow.runtime import Runtime

    h.task(start=False)
    state = h.runtime.state
    state.workers["w0"].roles = ("executor",)
    h.runtime = Runtime(state)
    assert make_worker(h, LocalClient(h)).once() == {"status": "idle"}
    assert h.runtime.state.tasks["t"].status == "ready"
