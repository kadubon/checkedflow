"""Independent timestamp/root oracle for bounded storage lifecycle sequences."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from checkedflow.artifact_io import Access
from checkedflow.artifacts import LocalStore
from checkedflow.core.artifact import Reference
from checkedflow.core.values import Failure
from checkedflow.retention import RetentionStore


class RetentionModel(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.temporary = TemporaryDirectory(prefix="checkedflow-retention-model-")
        root = Path(self.temporary.name)
        self.access = Access(
            "model-owner",
            frozenset({"model"}),
            frozenset({"read", "write", "pin", "maintain", "erase"}),
        )
        self.store = RetentionStore(
            root / "catalog.sqlite",
            LocalStore(root / "bytes.sqlite"),
            namespace="model",
            scope="model",
            trusted_floor=0,
            object_limit=3,
            retention_blocks=2,
            grace_blocks=3,
        )
        self.height = 0
        self.writes = {}
        self.releases = {}
        self.retired = set()
        self.roots = {}

    @staticmethod
    def reference(index):
        body = bytes([index])
        return Reference(
            "sha256",
            sha256(body).hexdigest(),
            1,
            "application/octet-stream",
            "evidence",
            "model",
            "a" * 64,
        )

    @rule(index=st.integers(0, 5))
    def publish(self, index):
        if index in self.retired:
            error = "RETIRED_ARTIFACT"
        elif index not in self.writes and len(self.writes) >= 3:
            error = "CAPACITY"
        else:
            error = None
        if error:
            with pytest.raises(Failure, match=error):
                self.store.put(self.reference(index), BytesIO(bytes([index])), access=self.access)
        else:
            self.store.put(self.reference(index), BytesIO(bytes([index])), access=self.access)
            self.writes[index] = self.height

    @rule(index=st.integers(0, 5), operation=st.integers(0, 2))
    def protect(self, index, operation):
        if index not in self.writes:
            return
        if operation in self.roots and self.roots[operation][0] != index:
            with pytest.raises(Failure, match="CONFLICT"):
                self.store.pin(
                    str(operation), (self.reference(index),), category="pending", access=self.access
                )
            return
        pin = self.store.pin(
            str(operation), (self.reference(index),), category="pending", access=self.access
        )
        self.roots[operation] = index, pin

    @rule(operation=st.integers(0, 2))
    def release(self, operation):
        if operation not in self.roots:
            return
        index, pin = self.roots.pop(operation)
        self.store.release(pin, access=self.access)
        self.releases[index] = self.height

    @rule(delta=st.integers(0, 5))
    def advance(self, delta):
        self.height += delta
        self.store.advance(self.height, access=self.access)

    def eligible(self):
        protected = {index for index, _ in self.roots.values()}
        return {
            index
            for index, written in self.writes.items()
            if index not in protected
            and self.height >= max(written + 2, self.releases.get(index, 0)) + 3
        }

    @rule()
    def reclaim(self):
        eligible = self.eligible()
        plan = self.store.plan(access=self.access)
        assert {item.digest for item in plan.objects} == {
            self.reference(index).digest for index in eligible
        }
        if eligible:
            results = self.store.sweep(plan, access=self.access)
            assert all(result.status == "erased" for result in results)
            for index in eligible:
                self.writes.pop(index)
                self.retired.add(index)

    @invariant()
    def protected_data_and_retirement_agree(self):
        for index in self.writes:
            assert self.store.get(self.reference(index), access=self.access) == bytes([index])
        for index in self.retired:
            with pytest.raises(Failure, match="RETIRED_ARTIFACT"):
                self.store.get(self.reference(index), access=self.access)
        assert len(self.writes) <= 3
        assert self.store.revision(access=self.access) > 0

    def teardown(self):
        self.temporary.cleanup()


TestRetentionModel = RetentionModel.TestCase
TestRetentionModel.settings = settings(max_examples=12, stateful_step_count=35, deadline=None)
