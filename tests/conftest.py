from __future__ import annotations

from hashlib import sha256

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from checkedflow.core.model import genesis
from checkedflow.identity import public_key, sign
from checkedflow.runtime import Runtime
from checkedflow.synthesis import CONTRACT, behavior_digest, domain, interpret, source_for

IMAGE = "python@sha256:" + "a" * 64


class Harness:
    def __init__(self, *, budget=10000, exhaustive=True, bootstrap=True):
        # Public test-only keys. Never use these identities in a deployment.
        self.keys = {
            name: Ed25519PrivateKey.from_private_bytes(sha256(name.encode()).digest())
            for name in [*(f"org{i}" for i in range(4)), *(f"w{i}" for i in range(4))]
        }
        self.initial = genesis(
            "checkedflow-test", {f"org{i}": public_key(self.keys[f"org{i}"]) for i in range(4)}
        )
        self.runtime = Runtime(self.initial)
        self.events = []
        if bootstrap:
            for i in range(4):
                self.apply(
                    "worker.register",
                    {
                        "id": f"w{i}",
                        "key": public_key(self.keys[f"w{i}"]),
                        "organization": f"org{i}",
                        "roles": ["producer", "executor", "verifier"],
                    },
                    admin=True,
                )
            self.apply(
                "verifier.register",
                {
                    "id": "v1",
                    "contract": CONTRACT,
                    "image": IMAGE,
                    "argv": ["python", "-B", "-s", "/work/check.py"],
                    "exhaustive": exhaustive,
                },
                admin=True,
            )
            self.apply("mission.create", self.mission(budget=budget), admin=True)

    @staticmethod
    def mission(**changes):
        return {
            "id": "m",
            "workers": [f"w{i}" for i in range(4)],
            "budget": 10000,
            "verification_reserve": 4,
            "verification_cost": 1,
            "expires": 10000,
            "max_rounds": 3,
            "max_candidates": 16,
            "max_depth": 3,
            "max_attempts": 3,
            "verifier": "v1",
            "receiver": "array-user",
            "contract": CONTRACT,
            "image": IMAGE,
        } | changes

    def envelope(self, kind, payload, *, actor="w0", admin=False, command_id=None):
        if admin:
            actor = "org0"
        state = self.runtime.state
        command = {
            "api_version": "checkedflow/v1",
            "chain": state.chain,
            "id": command_id or f"command-{len(state.processed)}",
            "actor": actor,
            "nonce": state.nonces.get(actor, 0) + 1,
            "kind": kind,
            "payload": payload,
        }
        keys = {name: self.keys[name] for name in (["org0", "org1", "org2"] if admin else [actor])}
        return sign(command, keys)

    def apply(self, kind, payload, *, actor="w0", admin=False, height=None):
        envelope = self.envelope(kind, payload, actor=actor, admin=admin)
        height = height or self.runtime.state.height + 1
        state = self.runtime.apply(envelope, height=height)
        self.events.append((envelope, height))
        return state

    def task(
        self,
        identity="t",
        *,
        dependencies=(),
        phase="generate",
        effect="isolated",
        cost=2,
        spec=None,
        start=True,
        finish=True,
    ):
        self.apply(
            "task.create",
            {
                "id": identity,
                "mission": "m",
                "phase": phase,
                "spec": spec or {"target": ["double"]},
                "dependencies": list(dependencies),
                "cost": cost,
                "ttl": 1000,
                "effect": effect,
            },
        )
        if start:
            self.apply("task.lease", {"id": identity})
            self.apply("task.start", {"id": identity, "fence": 1})
            if finish:
                self.apply(
                    "task.finish",
                    {
                        "id": identity,
                        "fence": 1,
                        "result": {
                            "outcome": "reported",
                            "evidence": {
                                "producer_report": True,
                                "source_digest": sha256(
                                    source_for(("double",)).encode()
                                ).hexdigest(),
                            },
                        },
                    },
                )

    def propose(
        self, identity="c", task="t", *, operations=("double",), origin="generated", expires=9000
    ):
        return self.apply(
            "capability.propose",
            {
                "id": identity,
                "task": task,
                "source": source_for(operations),
                "expires": expires,
                "origin": origin,
            },
        )

    def vote(self, cid="c", index=0, *, outcome="pass", behavior=None):
        tid, actor = f"verify:{cid}:org{index}", f"w{index}"
        self.apply("task.lease", {"id": tid}, actor=actor)
        fence = self.runtime.state.tasks[tid].fence
        self.apply("task.start", {"id": tid, "fence": fence}, actor=actor)
        cap = self.runtime.state.capabilities[cid]
        exhaustive = self.runtime.state.verifiers["v1"].exhaustive
        fingerprint = behavior or behavior_digest([interpret(xs, ("double",)) for xs in domain()])
        return self.apply(
            "task.finish",
            {
                "id": tid,
                "fence": fence,
                "result": {
                    "outcome": outcome,
                    "evidence": {
                        "source_digest": cap.source_digest,
                        "contract": CONTRACT,
                        "verifier": "v1",
                        "image": IMAGE,
                        "behavior": fingerprint if outcome == "pass" and exhaustive else None,
                        "reason": "test-only finite-domain observation",
                    },
                },
            },
            actor=actor,
        )

    def checked(self, cid="c", tid="t", **kwargs):
        self.task(tid, **kwargs)
        self.propose(cid, tid)
        for i in range(3):
            self.vote(cid, i)
        return self.runtime.state


@pytest.fixture
def h():
    return Harness()
