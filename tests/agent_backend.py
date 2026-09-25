"""Test-only adapter. This in-process ledger is not a distributed deployment."""

from conftest import Harness

from checkedflow.agents.gateway import Gateway


class Backend:
    def __init__(self, h=None):
        self.h = h or Harness()
        self.calls = 0
        self.lose_response = False

    def state(self):
        return self.h.runtime.state

    def submit(self, envelope):
        self.calls += 1
        self.h.runtime.apply(envelope, height=self.h.runtime.state.height + 1)
        if self.lose_response:
            raise TimeoutError("response lost after commit")
        return {"accepted": True}

    def gateway(self):
        return Gateway(self, self.h.runtime.state.chain, "m")
