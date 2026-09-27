"""The accounting report must not reuse a pre-barrier state snapshot."""

from types import SimpleNamespace

import pytest

from checkedflow.core.values import Failure
from checkedflow.distributed.demo import _final_state


def test_final_accounting_reads_again_after_last_worker_commit():
    class Cluster:
        settled = False

        def client(self, index=0):
            def state():
                if self.settled:
                    return SimpleNamespace(height=103, spent=780)
                return SimpleNamespace(height=101 if index == 3 else 100, spent=779)

            return SimpleNamespace(state=state)

        def wait_height(self, height):
            assert height == 103
            self.settled = True

    assert _final_state(Cluster()).spent == 780


def test_final_accounting_rejects_a_stale_post_barrier_node():
    client = SimpleNamespace(state=lambda: SimpleNamespace(height=100))
    cluster = SimpleNamespace(client=lambda index=0: client, wait_height=lambda height: None)
    with pytest.raises(Failure, match="precedes final barrier"):
        _final_state(cluster)
