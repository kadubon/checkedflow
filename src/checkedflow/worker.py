"""Worker orchestration outside consensus. Every execution has a committed start record."""

from collections.abc import Callable
from hashlib import sha256

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from checkedflow.core.model import State, Task
from checkedflow.core.values import Failure, Object, array, obj, require, text
from checkedflow.distributed.client import Client
from checkedflow.identity import public_key, sign
from checkedflow.runner import GVisorRunner
from checkedflow.synthesis import (
    CONTRACT,
    LibraryEntry,
    behavior_digest,
    domain,
    interpret,
    synthesize,
)
from checkedflow.wire import digest, validate


class Worker:
    def __init__(
        self,
        client: Client,
        identity: str,
        key: Ed25519PrivateKey,
        chain: str,
        runner_factory: Callable[[str], GVisorRunner] = GVisorRunner,
    ) -> None:
        self.client, self.identity, self.key = client, identity, key
        self.chain, self.runner_factory = chain, runner_factory

    def _state(self) -> State:
        state = self.client.state()
        require(state.chain == self.chain, "CHAIN", "worker chain mismatch")
        require(self.identity in state.workers, "AUTHORITY", "worker is not registered")
        record = state.workers[self.identity]
        require(
            not record.revoked and record.key == public_key(self.key),
            "AUTHORITY",
            "worker key mismatch",
        )
        return state

    def send(self, kind: str, payload: Object) -> None:
        state = self._state()
        nonce = state.nonces.get(self.identity, 0) + 1
        command: Object = {
            "api_version": "checkedflow/v1",
            "chain": self.chain,
            "actor": self.identity,
            "nonce": nonce,
            "id": digest(
                {"actor": self.identity, "nonce": nonce, "kind": kind, "payload": payload}
            ),
            "kind": kind,
            "payload": payload,
        }
        self.client.submit(sign(command, {self.identity: self.key}))

    def once(self) -> Object:
        state = self._state()
        worker = state.workers[self.identity]
        proposed = {cap.task for cap in state.capabilities.values()}
        for tid, task in sorted(state.tasks.items()):
            if (
                task.phase == "generate"
                and task.status == "finished"
                and task.owner == self.identity
                and task.result.get("outcome") == "reported"
                and "source" in obj(task.result.get("evidence", {}))
                and tid not in proposed
                and state.height < state.missions[task.mission].expires
                and "producer" in worker.roles
            ):
                self.propose(state, tid, task)
                return {"task": tid, "status": "proposed", "resumed": True}
        eligible = [
            (tid, task)
            for tid, task in state.tasks.items()
            if task.status == "ready"
            and self.identity in state.missions[task.mission].workers
            and state.height < state.missions[task.mission].expires
            and (not task.organization or task.organization == worker.organization)
            and ("verifier" if task.phase == "verify" else "executor") in worker.roles
            and (task.phase != "generate" or "producer" in worker.roles)
        ]
        if not eligible:
            return {"status": "idle"}
        tid, _ = min(eligible, key=lambda item: (item[1].phase != "verify", item[0]))
        self.send("task.lease", {"id": tid})
        task = self._state().tasks[tid]
        fence = task.fence
        self.send("task.start", {"id": tid, "fence": fence})
        state = self._state()
        task = state.tasks[tid]
        require(
            task.owner == self.identity
            and task.fence == fence
            and task.status == "running"
            and state.height < task.deadline,
            "LEASE",
            "execution start is not current",
        )
        try:
            result, candidate = self.execute(state, task)
        except (Failure, OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
            if task.phase == "verify":
                result = self.verification_result(state, task, "unknown", None, str(exc)[:4096])
            else:
                result = {"outcome": "unknown", "evidence": {"reason": str(exc)[:4096]}}
            candidate = None
        if candidate is not None:
            # Persist the exact candidate before proposing it. A restart never regenerates it.
            obj(result["evidence"])["source"] = candidate
        self.send("task.finish", {"id": tid, "fence": fence, "result": result})
        if candidate is not None:
            committed = self._state()
            self.propose(committed, tid, committed.tasks[tid])
        return {"task": tid, "outcome": result["outcome"], "fence": fence}

    def propose(self, state: State, tid: str, task: Task) -> None:
        """Resume metadata admission from a committed receipt without executing code again."""
        evidence = obj(task.result["evidence"])
        source = text(evidence["source"], limit=262144)
        require(
            sha256(source.encode("utf-8")).hexdigest() == evidence.get("source_digest"),
            "BINDING",
            "persisted candidate differs from its receipt",
        )
        self.send(
            "capability.propose",
            {
                "id": "cap:" + digest({"source": source, "task": tid}),
                "task": tid,
                "source": source,
                "expires": min(
                    [state.missions[task.mission].expires - 1]
                    + [state.capabilities[d].expires for d in task.dependencies]
                ),
                "origin": "generated",
            },
        )

    def execute(self, state: State, task: Task) -> tuple[Object, str | None]:
        require(task.effect == "isolated", "EFFECT", "v1 worker does not dispatch external effects")
        mission = state.missions[task.mission]
        if task.phase == "generate":
            if "generator_argv" in task.spec:
                runner = self.runner_factory(mission.image)
                argv = tuple(text(v) for v in array(task.spec["generator_argv"], limit=32))
                result = runner.generate(argv, obj(task.spec["request"]))
                source = text(result["source"], limit=262144)
                result["source_digest"] = sha256(source.encode("utf-8")).hexdigest()
                return {"outcome": "reported", "evidence": result}, source
            target = tuple(text(v) for v in array(task.spec["target"], limit=4))
            library = tuple(
                LibraryEntry(
                    cid,
                    tuple(
                        text(op)
                        for op in array(
                            state.tasks[state.capabilities[cid].task].spec["target"], limit=4
                        )
                    ),
                    state.capabilities[cid].source,
                )
                for cid in task.dependencies
            )
            program = synthesize(
                target,
                max_candidates=mission.max_candidates,
                max_depth=min(mission.max_depth, 4),
                library=library,
            )
            require(
                set(program.dependencies) == set(task.dependencies),
                "DEPENDENCY",
                "unused dependency",
            )
            return {
                "outcome": "reported",
                "evidence": {
                    "candidates_tried": program.candidates_tried,
                    "operations": list(program.operations),
                    "source_digest": sha256(program.source.encode()).hexdigest(),
                },
            }, program.source
        if task.phase == "verify":
            cap = state.capabilities[task.subject]
            verifier = state.verifiers[mission.verifier]
            require(
                mission.contract == CONTRACT
                and verifier.argv == ("python", "-B", "-s", "/work/check.py"),
                "VERIFIER",
                "reference worker requires the integer-array verifier",
            )
            target = tuple(text(op) for op in array(state.tasks[cap.task].spec["target"], limit=4))
            expected = array(validate([interpret(values, target) for values in domain()]))
            runner = self.runner_factory(verifier.image)
            actual = runner.python(cap.source, [list(xs) for xs in domain()])
            outcome = "pass" if actual == expected else "fail"
            behavior = (
                behavior_digest(actual) if outcome == "pass" and verifier.exhaustive else None
            )
            return self.verification_result(
                state, task, outcome, behavior, "all 31 declared inputs checked"
            ), None
        argv = tuple(text(v) for v in array(task.spec["argv"], limit=32))
        runner = self.runner_factory(mission.image)
        execution = runner.run(argv, {}, task.spec.get("input"))
        return {
            "outcome": execution.status,
            "evidence": {
                "reason": execution.reason,
                "exit_code": execution.returncode,
                "stdout_digest": sha256(execution.stdout).hexdigest(),
                "stderr_digest": sha256(execution.stderr).hexdigest(),
            },
        }, None

    @staticmethod
    def verification_result(
        state: State, task: Task, outcome: str, behavior: str | None, reason: str
    ) -> Object:
        cap = state.capabilities[task.subject]
        mission = state.missions[task.mission]
        return {
            "outcome": outcome,
            "evidence": {
                "source_digest": cap.source_digest,
                "contract": mission.contract,
                "verifier": mission.verifier,
                "image": state.verifiers[mission.verifier].image,
                "behavior": behavior,
                "reason": reason,
            },
        }
