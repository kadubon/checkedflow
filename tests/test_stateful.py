"""Independent lifecycle reference coordinates under generated operation sequences."""

from conftest import Harness
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from checkedflow.runtime import Runtime


class LifecycleModel(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.h = Harness()
        self.live = set()
        self.dependents = {}
        self.expected_spent = 0
        self.expected_residuals = set()
        self.next_id = 0

    @rule(use_parent=st.booleans())
    def form(self, use_parent):
        if self.next_id >= 4:
            return
        identity = f"c{self.next_id}"
        task = f"g{self.next_id}"
        parents = tuple(sorted(self.live)[:1]) if use_parent else ()
        self.h.checked(identity, task, dependencies=parents)
        self.live.add(identity)
        self.dependents[identity] = set(parents)
        self.expected_spent += 5
        self.next_id += 1

    @rule()
    def revoke_oldest(self):
        if not self.live:
            return
        identity = min(self.live)
        affected = {identity}
        changed = True
        while changed:
            prior = set(affected)
            affected |= {child for child, parents in self.dependents.items() if parents & affected}
            changed = prior != affected
        self.h.apply(
            "capability.revoke", {"id": identity, "reason": "reference incident"}, admin=True
        )
        self.live -= affected
        self.expected_residuals |= {f"invalid:{child}:{identity}" for child in affected}

    @rule()
    def duplicate_last(self):
        envelope, height = self.h.events[-1]
        before = self.h.runtime.state_hash
        self.h.runtime.apply(envelope, height=height)
        assert self.h.runtime.state_hash == before

    @invariant()
    def model_and_replay_agree(self):
        state = self.h.runtime.state
        assert {
            cid for cid, cap in state.capabilities.items() if cap.status == "checked"
        } == self.live
        assert state.missions["m"].spent == self.expected_spent
        assert set(state.residuals) == self.expected_residuals
        assert state.missions["m"].reserved == len(self.live)
        replay = Runtime(self.h.initial)
        for envelope, height in self.h.events:
            replay.apply(envelope, height=height)
        assert replay.state_hash == self.h.runtime.state_hash


TestLifecycle = LifecycleModel.TestCase
TestLifecycle.settings = settings(max_examples=8, stateful_step_count=10, deadline=None)
