"""Check wheel/sdist contents and installed behavior outside the source checkout."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "checkedflow/py.typed",
    "checkedflow/data/envelope.schema.json",
    "checkedflow/data/state.schema.json",
    "checkedflow/data/generator.schema.json",
    "checkedflow/data/block.schema.json",
    "checkedflow/data/agent-request.schema.json",
    "checkedflow/data/agent-vectors.json",
    "checkedflow/data/agents.json",
    "checkedflow/data/callback-keyring.schema.json",
    "checkedflow/data/artifact-publication.schema.json",
    "checkedflow/data/access-policy.schema.json",
    "checkedflow/data/access-roles.json",
    "checkedflow/data/access-vectors.json",
    "checkedflow/data/commands.json",
    "checkedflow/data/vectors.json",
    "checkedflow/data/legacy-v1.json",
    "checkedflow/data/legacy-successor-vector.json",
    "checkedflow/data/succession.schema.json",
    "checkedflow/data/succession-vector.json",
    "checkedflow/data/operational-envelope.schema.json",
    "checkedflow/data/operational-state.schema.json",
    "checkedflow/data/request-archive.schema.json",
    "checkedflow/data/work-archive.schema.json",
    "checkedflow/data/work-archive-command.schema.json",
    "checkedflow/data/work-archive-vector.json",
    "checkedflow/data/artifact-reference.schema.json",
    "checkedflow/data/retention-plan.schema.json",
    "checkedflow/data/retention-vectors.json",
    "checkedflow/data/retention-checkpoint.schema.json",
    "checkedflow/data/application-checkpoint.schema.json",
    "checkedflow/data/application-backup-vector.json",
    "checkedflow/data/application-recovery.json",
    "checkedflow/data/worker-operations.json",
    "checkedflow/data/sandbox-recovery.service",
    "checkedflow/data/retention-backup-vector.json",
    "checkedflow/data/github-draft-plan.schema.json",
    "checkedflow/data/github-draft-vector.json",
    "checkedflow/data/git-tree-vectors.json",
    "checkedflow/data/key-command.schema.json",
    "checkedflow/data/budget-command.schema.json",
    "checkedflow/data/task-command.schema.json",
    "checkedflow/data/acceptance-command.schema.json",
    "checkedflow/data/effect-command.schema.json",
    "checkedflow/data/effect-reconciliation.schema.json",
    "checkedflow/data/effect-staging-policy.schema.json",
    "checkedflow/data/artifact-availability.schema.json",
    "checkedflow/data/service-observation.schema.json",
    "checkedflow/data/operation-observation.schema.json",
    "checkedflow/data/monitoring/alerts.yml",
    "checkedflow/data/monitoring/alert-tests.yml",
    "checkedflow/data/monitoring/prometheus.yml.example",
    "checkedflow/data/monitoring/dashboard.json",
    "checkedflow/data/github-effect-intent.schema.json",
    "checkedflow/data/effect-flow-vector.json",
    "checkedflow/data/operational-configuration.schema.json",
    "checkedflow/data/repository-patch.schema.json",
    "checkedflow/data/repository-tree.schema.json",
    "checkedflow/data/invoice-fixture.json",
    "checkedflow/data/repository-cases.schema.json",
    "checkedflow/data/research.json",
    "checkedflow/data/examples/sdk.py",
    "checkedflow/data/examples/agents.py",
    "checkedflow/distributed/proto/LICENSE-CometBFT",
    "checkedflow/distributed/proto/LICENSE-gogoproto",
    "checkedflow/distributed/proto/NOTICE-CometBFT",
]
SMOKE = """
import importlib.resources as r, json, runpy, sys, subprocess
import checkedflow
from pathlib import Path
origin = Path(checkedflow.__file__).resolve().relative_to(Path(sys.prefix).resolve())
assert "site-packages" in origin.parts
print("Installed import origin:", origin.as_posix())
for name, filename in (("alerts", "alerts.yml"), ("alert-tests", "alert-tests.yml"),
                       ("scrape", "prometheus.yml.example"), ("dashboard", "dashboard.json")):
    result = subprocess.run([sys.executable, "-I", "-m", "checkedflow.cli", "monitoring", name],
                            check=True, capture_output=True, timeout=15)
    resource = r.files("checkedflow").joinpath("data", "monitoring", filename)
    assert result.stdout == resource.read_bytes()
roles = subprocess.run([sys.executable, "-I", "-m", "checkedflow.cli", "schema", "access-roles"],
                       check=True, capture_output=True, timeout=15)
assert json.loads(roles.stdout)["roles"]["submit"] == [
    "task.lease", "task.start", "task.heartbeat", "task.finish", "effect.reserve", "effect.report"]
from checkedflow.operational_codec import decode as decode_control
from checkedflow.operational_runtime import Runtime as ControlRuntime
from checkedflow.wire import dumps as canonical
effect_vector = json.loads(
    r.files("checkedflow").joinpath("data/effect-flow-vector.json").read_text(encoding="utf-8")
)
effect_runtime = ControlRuntime(decode_control(canonical(effect_vector["initial"])))
for step in effect_vector["steps"]:
    effect_runtime.apply(canonical(step["envelope"]), height=step["height"])
    assert effect_runtime.state_hash == step["state_hash"]
assert effect_runtime.state.effects[0].status == effect_vector["final_status"]
assert effect_runtime.state.budget.spent == effect_vector["spent"]
state_schema = json.loads(
    r.files("checkedflow").joinpath("data/operational-state.schema.json").read_text()
)
config_schema = json.loads(
    r.files("checkedflow").joinpath("data/operational-configuration.schema.json").read_text()
)
assert config_schema["properties"]["state"] == state_schema
subprocess.run([sys.executable, "-I", "-m", "checkedflow.sandbox_recovery", "--help"],
               check=True, capture_output=True, timeout=15)
from checkedflow.dispatch_watchdog import Watchdog
from checkedflow.core.values import Failure
watchdog = Watchdog(lambda: None, chain="installed", mission="fixture",
                    max_read_age_ns=1, max_stall_ns=1)
try:
    watchdog.current()
except Failure as error:
    assert error.code == "NOT_READY"
else:
    raise AssertionError("cold watchdog granted readiness")
watchdog.stop()
from checkedflow.repository_execution import CHECKER_DIGEST, inventory
from checkedflow.wire import dumps
fixture = json.loads(r.files("checkedflow").joinpath("data/invoice-fixture.json").read_text())
request, expected = inventory(dumps(fixture["inventory"]))
assert len(expected) == 4 and request["path"] == "shop/invoice.py"
assert len(CHECKER_DIGEST) == 64
print("Installed repository checker:", CHECKER_DIGEST)
from checkedflow.repository_reuse import decode_tree, tree_bytes
from checkedflow.domains.repository_patch import Tree
bundle_tree = Tree((("empty.py", b""),))
assert decode_tree(tree_bytes(bundle_tree)) == bundle_tree
from checkedflow.retention import decode_plan
retention_vectors = json.loads(
    r.files("checkedflow").joinpath("data/retention-vectors.json").read_text()
)
for record in retention_vectors["valid"]:
    assert decode_plan(dumps(record)).record() == record
from io import BytesIO
from tempfile import TemporaryDirectory
from checkedflow.artifact_io import Access
from checkedflow.artifacts import LocalStore
from checkedflow.retention_backup import decode_checkpoint, export_catalog, restore_catalog
backup_vector = json.loads(
    r.files("checkedflow").joinpath("data/retention-backup-vector.json").read_text()
)
checkpoint = decode_checkpoint(dumps(backup_vector["checkpoint"]))
with TemporaryDirectory(prefix="checkedflow-installed-backup-") as temporary:
    directory = Path(temporary)
    access = Access("fixture", frozenset({"mission"}), frozenset({"restore", "backup"}))
    restored = restore_catalog(BytesIO(backup_vector["jsonl"].encode()),
        directory / "restored", LocalStore(directory / "bytes.sqlite"),
        checkpoint=checkpoint, current_revision=1, namespace="operator", scope="mission",
        access=access)
    output = BytesIO()
    assert export_catalog(restored, output, access=access) == checkpoint
    assert output.getvalue() == backup_vector["jsonl"].encode()
from checkedflow.runtime import Runtime
from checkedflow.serialization import decode
from checkedflow.wire import dumps, loads
from checkedflow.cli import main
with TemporaryDirectory(prefix="checkedflow-installed-history-cli-") as temporary:
    from checkedflow.operational_codec import state_bytes, decode as decode_control
    directory = Path(temporary)
    vector = json.loads(
        r.files("checkedflow").joinpath("data/application-backup-vector.json").read_text())
    (directory / "initial.json").write_bytes(state_bytes(decode_control(dumps(vector["initial"]))))
    (directory / "checkpoint.json").write_bytes(dumps(vector["checkpoint"]))
    (directory / "history.jsonl").write_text(vector["jsonl"], encoding="utf-8", newline="")
    assert main(["application-backup", "restore", "--genesis", str(directory / "initial.json"),
        "--source", str(directory / "history.jsonl"),
        "--checkpoint", str(directory / "checkpoint.json"),
        "--current-height", "2", "--destination", str(directory / "restored")]) == 0
assert checkedflow.__version__ == "0.1.0"
v = json.loads(r.files("checkedflow").joinpath("data/vectors.json").read_text())
for row in v["canonical"]:
    assert dumps(loads(row["input"])).hex() == row["canonical_hex"]
runtime = Runtime(decode(v["lifecycle"]["genesis"]))
for event in v["lifecycle"]["events"]:
    runtime.apply(event["envelope"], height=event["height"])
assert runtime.state_hash == v["lifecycle"]["final_hash"]
suffix = v["lifecycle"]["empty_block_suffix"]
runtime.tick(suffix["height"])
assert runtime.state_hash == suffix["final_hash"]
from checkedflow.recovery import replay_blocks
legacy = json.loads(r.files("checkedflow").joinpath("data/legacy-v1.json").read_text())
assert replay_blocks(decode(legacy["initial"]), legacy["blocks"]).state_hash == (
    "9c12eea3393f018bbac48ad1660b1077c9e8fb6d1b6665eede9ca50cf1817950"
)
from checkedflow.legacy_inventory import Checkpoint, inspect_snapshot
old = legacy["final_state"]
inventory = inspect_snapshot(dumps(old), Checkpoint(
    old["chain"], old["height"], legacy["final_state_hash"]
))
assert loads(inventory.snapshot) == old
assert "unknown" in inventory.pending_tasks
assert {"c", "child"} <= set(inventory.withdrawn_capabilities)
assert inventory.open_residuals
from checkedflow.legacy_successor import prepare
successor = json.loads(r.files("checkedflow").joinpath(
    "data/legacy-successor-vector.json").read_text())
prepared = prepare(dumps(old), Checkpoint(**successor["checkpoint"]),
    decode_control(dumps(successor["initial"])), mission=successor["mission"])
assert loads(state_bytes(prepared)) == successor["successor"]
assert ControlRuntime(prepared).state_hash == successor["successor_hash"]
from checkedflow.succession import verify as verify_succession
approval_vector = json.loads(r.files("checkedflow").joinpath(
    "data/succession-vector.json").read_text())
approval = verify_succession(dumps(approval_vector["manifest"]), legacy=dumps(old),
    trusted=Checkpoint(**approval_vector["checkpoint"]),
    successor=decode_control(dumps(approval_vector["successor"])),
    validators=tuple(tuple(pair) for pair in approval_vector["validators"]))
assert approval.plan_hash == approval_vector["plan_hash"]
from checkedflow.succession_journal import ApprovalJournal
import tempfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
plan = approval_vector["manifest"]["plan"]
with tempfile.TemporaryDirectory() as approval_directory:
    approval_path = Path(approval_directory) / "approval.db"
    binding = dict(chain=plan["old"]["chain"], mission=plan["old"]["mission"],
        side="new", organization="a", identity="a")
    journal = ApprovalJournal(approval_path, **binding, create=True)
    test_signer = Ed25519PrivateKey.from_private_bytes(bytes(32))
    first = journal.sign(plan, test_signer)
    journal.close()
    journal = ApprovalJournal(approval_path, **binding)
    assert journal.sign(plan, test_signer) == first
    journal.close()
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from checkedflow.operational_identity import Credential, authenticate, sign_command
keys = {(name, 1): Ed25519PrivateKey.generate() for name in ("a", "b", "c", "d")}
registry = {
    (name, revision): Credential(
        name, revision, name, "administrator", "", key.public_key().public_bytes_raw().hex(), 0
    )
    for (name, revision), key in keys.items()
}
command = {
    "api_version": "checkedflow/v2", "chain": "installed-auth-only", "epoch": 0,
    "id": "auth-check", "actor": "a", "revision": 1, "nonce": 1,
    "kind": "mission.pause", "payload": {"mission": "m"}
}
raw = sign_command(command, {identity: key for identity, key in keys.items() if identity[0] != "d"})
parsed, verified = authenticate(
    raw, chain="installed-auth-only", epoch=0, height=0,
    organizations=frozenset("abcd"), registry=registry
)
assert parsed == command
verified.require_administration()
from checkedflow.core.operational import genesis as operational_genesis
from checkedflow.operational_runtime import Runtime as OperationalRuntime
control = OperationalRuntime(operational_genesis(
    "installed-auth-only", "m", tuple("abcd"), tuple(registry.values())
))
command["id"] = "0:pause"
control.apply(sign_command(command, keys), height=1)
assert control.state.mode == "paused"
command.update(id="0:checkpoint", nonce=2, kind="journal.rollover")
archive = control.apply(sign_command(command, keys), height=2)
assert archive.root == control.state.journal.archive_root
assert control.state.journal.epoch == 1
for offset, (kind, payload) in enumerate([
    ("budget.configure", {"budget": 100, "verification_reserve": 20}),
    ("mission.resume", {}),
    ("budget.reserve", {"phase": "execute", "ceiling": 60, "target": "a" * 64}),
    ("budget.settle", {"ticket": "1:budget-5", "outcome": "unknown", "charged": 60}),
], start=3):
    funding = command | {"epoch": 1, "id": f"1:budget-{offset}", "nonce": offset,
                         "kind": kind, "payload": {"mission": "m"} | payload}
    control.apply(sign_command(funding, keys), height=offset)
assert control.state.budget.spent == 60 and control.state.budget.available == 40
from checkedflow.operational_codec import decode as decode_control, state_bytes
assert decode_control(state_bytes(control.state)) == control.state
worker_key = Ed25519PrivateKey.generate()
worker_credential = Credential("executor", 1, "a", "executor", "m",
                              worker_key.public_key().public_bytes_raw().hex(), 0)
work = OperationalRuntime(operational_genesis(
    "installed-auth-only", "m", tuple("abcd"), (*registry.values(), worker_credential)
))
for number, (kind, payload) in enumerate([
    ("budget.configure", {"budget": 100, "verification_reserve": 20}),
    ("mission.resume", {}),
    ("budget.reserve", {"phase": "execute", "ceiling": 30, "target": "a" * 64}),
    ("task.admit", {"ticket": "0:task-3", "workers": ["executor"], "lease_blocks": 5,
                    "expires": 100, "max_attempts": 2}),
], start=1):
    raw = sign_command(command | {"epoch": 0, "id": f"0:task-{number}", "nonce": number,
        "kind": kind, "payload": {"mission": "m"} | payload}, keys)
    work.apply(raw, height=number)
for nonce, (kind, extra) in enumerate([
    ("task.lease", {}), ("task.start", {"fence": 1}),
    ("task.finish", {"fence": 1, "outcome": "reported", "evidence": "b" * 64}),
], start=1):
    raw = sign_command(command | {"epoch": 0, "id": f"0:worker-{nonce}", "nonce": nonce,
        "actor": "executor", "kind": kind,
        "payload": {"mission": "m", "task": "0:task-4"} | extra},
        {("executor", 1): worker_key})
    work.apply(raw, height=nonce + 4)
assert work.state.tasks[0].status == "finished" and work.state.budget.spent == 30
assert decode_control(state_bytes(work.state)) == work.state
from checkedflow.worker_submission import Coordinator
from checkedflow.worker_supervisor import Supervisor, Outcome
from checkedflow.repository_worker import RepositoryExecutor, EvidencePublisher
from checkedflow.core.work_archive import WorkArchive
from checkedflow.operational_codec import work_archive_bytes
retirement = json.loads(
    r.files("checkedflow").joinpath("data/work-archive-vector.json").read_text())
retirement_runtime = OperationalRuntime(decode_control(dumps(retirement["initial"])))
retired_batches = []
for event in retirement["events"]:
    batch = retirement_runtime.apply(dumps(event["envelope"]), height=event["height"])
    if isinstance(batch, WorkArchive):
        retired_batches.append(
            {"root": batch.root, "record": json.loads(work_archive_bytes(batch))})
assert retirement_runtime.state_hash == retirement["state_hash"]
assert retired_batches == retirement["archives"]

import tempfile
from pathlib import Path
from checkedflow.operational_storage import Store
initial_control = operational_genesis(
    "installed-auth-only", "m", tuple("abcd"), tuple(registry.values())
)
with tempfile.TemporaryDirectory() as folder:
    store = Store(Path(folder) / "control.sqlite", initial_control)
    command.update(id="0:stored", nonce=1)
    committed = store.commit_block(
        1, [sign_command(command, keys)],
        previous_hash=OperationalRuntime(initial_control).state_hash
    )
    assert store.verify_history(expected_hash=committed.state_hash) == store.load()
    from checkedflow.operational_backup import export_history, restore_history
    history_output = BytesIO()
    checkpoint = export_history(store, history_output, expected_hash=committed.state_hash)
    recovered_application = restore_history(BytesIO(history_output.getvalue()),
        Path(folder) / "restored-application", initial=initial_control,
        checkpoint=checkpoint, current_height=store.load().height)
    assert recovered_application.load() == store.load()
    from checkedflow.operational_identity import prove_possession
    replacement = Ed25519PrivateKey.generate()
    proposal = command | {
        "epoch": 1, "id": "1:rotation", "nonce": 2, "kind": "key.schedule",
        "payload": {"mission": "m", "identity": "a", "revision": 2,
                    "public_key": replacement.public_key().public_bytes_raw().hex(),
                    "activation_height": 3, "proof": ""}
    }
    proposal = prove_possession(proposal, replacement)
    committed = store.commit_block(
        2, [sign_command(proposal, keys)], previous_hash=committed.state_hash
    )
    new_signers = {pair: signer for pair, signer in keys.items() if pair[0] != "a"}
    new_signers[("a", 2)] = replacement
    after = command | {"epoch": 1, "id": "1:after", "revision": 2,
                       "nonce": 3, "kind": "mission.pause"}
    committed = store.commit_block(
        3, [sign_command(after, new_signers)], previous_hash=committed.state_hash
    )
    assert committed.outcomes == ("OK",)
    assert store.verify_history(expected_hash=committed.state_hash) == store.load()
    lost = Ed25519PrivateKey.generate()
    pending = after | {
        "id": "1:pending", "nonce": 4, "kind": "key.schedule",
        "payload": {"mission": "m", "identity": "a", "revision": 3,
                    "public_key": lost.public_key().public_bytes_raw().hex(),
                    "activation_height": 100, "proof": ""}
    }
    committed = store.commit_block(
        4, [sign_command(prove_possession(pending, lost), new_signers)],
        previous_hash=committed.state_hash
    )
    revoke = after | {
        "id": "1:lost", "nonce": 5, "kind": "key.revoke",
        "payload": {"mission": "m", "identity": "a", "revision": 3, "reason": "lost"}
    }
    committed = store.commit_block(
        5, [sign_command(revoke, new_signers)], previous_hash=committed.state_hash
    )
    recovered = Ed25519PrivateKey.generate()
    recovery = pending | {
        "id": "1:recover", "nonce": 6,
        "payload": pending["payload"] | {"revision": 4, "activation_height": 7,
                     "public_key": recovered.public_key().public_bytes_raw().hex()}
    }
    committed = store.commit_block(
        6, [sign_command(prove_possession(recovery, recovered), new_signers)],
        previous_hash=committed.state_hash
    )
    del new_signers[("a", 2)]
    new_signers[("a", 4)] = recovered
    committed = store.commit_block(
        7, [sign_command(after | {"id": "1:recovered", "revision": 4, "nonce": 7}, new_signers)],
        previous_hash=committed.state_hash
    )
    assert committed.outcomes == ("OK",)
    assert store.verify_history(expected_hash=committed.state_hash) == store.load()
from io import BytesIO
from hashlib import sha256
from checkedflow.artifacts import Access, LocalStore
from checkedflow.core.artifact import Reference
with tempfile.TemporaryDirectory() as folder:
    artifacts = LocalStore(Path(folder) / "artifacts.sqlite")
    access = Access("installed-check", frozenset({"demo"}), frozenset({"read", "write"}))
    ref = Reference("sha256", sha256(b"installed").hexdigest(), 9,
                    "text/plain", "evidence", "demo", "1" * 64)
    artifacts.put(ref, BytesIO(b"installed"), access=access)
    assert artifacts.get(ref, access=access) == b"installed"
runpy.run_path(str(r.files("checkedflow").joinpath("data/examples/sdk.py")), run_name="__main__")
assert main(["example"]) == 0
from checkedflow.agents.gateway import profile
assert profile()["command_protocol"] == "checkedflow/v1"
assert main(["schema", "agent-request"]) == 0
print("Installed package smoke passed on", sys.version.split()[0])
"""
AGENT_SMOKE = """
import asyncio, sys
import httpx
from a2a.client import A2ACardResolver
from mcp import Client
from checkedflow.agents.a2a import create_app
from checkedflow.agents.gateway import Gateway
from checkedflow.agents.mcp import create_server
from checkedflow.github_drafts import Drafts, Token, decode_plan
from checkedflow.effect_dispatch import Dispatcher, Policy
from checkedflow.effect_supervisor import Supervisor as EffectSupervisor
from checkedflow.core.values import Failure
from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.resources as resources, json
from checkedflow.git_tree import tree_id
from checkedflow.domains.repository_patch import Tree
git_vectors = json.loads(
    resources.files("checkedflow").joinpath("data/git-tree-vectors.json").read_text(encoding="utf-8")
)
for case in git_vectors["cases"]:
    tree = Tree(tuple((item["path"], item["utf8"].encode()) for item in case["files"]))
    assert tree_id(tree) == case["git_tree"]
vector = json.loads(
    resources.files("checkedflow").joinpath("data/github-draft-vector.json").read_text()
)
from checkedflow.wire import dumps
from checkedflow.distributed.operational_application import Configuration, serve
from checkedflow.operational_codec import decode as decode_control
from checkedflow.succession import Succession, authorize_startup
from checkedflow.legacy_inventory import Checkpoint
startup = json.loads(resources.files("checkedflow").joinpath(
    "data/succession-vector.json").read_text())
legacy = json.loads(resources.files("checkedflow").joinpath("data/legacy-v1.json").read_text())
configuration = Configuration(decode_control(dumps(startup["successor"])),
    tuple(tuple(pair) for pair in startup["validators"]))
evidence = Succession(dumps(startup["manifest"]), dumps(legacy["final_state"]),
    Checkpoint(**startup["checkpoint"]))
authorize_startup(configuration.initial, configuration.validators, evidence)
with TemporaryDirectory() as directory:
    database = Path(directory) / "unapproved.sqlite"
    try:
        serve(database, configuration, "127.0.0.1:26658")
    except Failure as error:
        assert error.code == "BINDING"
    else:
        raise AssertionError("unapproved inherited startup accepted")
    assert not database.exists()
assert Dispatcher.__module__ == "checkedflow.effect_dispatch"
assert EffectSupervisor.__module__ == "checkedflow.effect_supervisor"
policy_schema = json.loads(
    resources.files("checkedflow").joinpath("data/effect-policy.schema.json").read_text()
)
assert policy_schema["properties"]["profile"]["const"] == "checkedflow/effect-policy/v1"
with TemporaryDirectory(prefix="checkedflow-disabled-draft-") as directory:
    policy = Policy(Path(directory) / "operator-owned.json")
    journal = Path(directory) / "never-created.sqlite"
    provider = Drafts("owner/fixture", 42, "owner", Token("fixture"), journal)
    try:
        provider.dispatch(decode_plan(dumps(vector)))
    except Failure as error:
        assert error.code == "DISABLED"
    else:
        raise AssertionError("draft provider must default to disabled")
    assert not journal.exists()
class NoNode:
    def state(self):
        raise RuntimeError("discovery must not require ledger authority")
    def submit(self, envelope):
        raise RuntimeError("discovery must never submit work")
async def main():
    gateway = Gateway(NoNode(), "discovery-only", "example")
    async with Client(create_server(gateway)) as client:
        assert len((await client.list_tools()).tools) == 2
        assert len((await client.list_resources()).resources) == 3
        assert len((await client.list_resource_templates()).resource_templates) == 3
        assert (await client.list_prompts()).prompts[0].name == "checkedflow_review"
    app = create_app(gateway, "http://127.0.0.1/rpc", "test-only-installation-token-00000000")
    async with (
        app.app.router.lifespan_context(app.app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app)) as transport,
    ):
        card = await A2ACardResolver(transport, "http://127.0.0.1").get_agent_card()
        assert card.supported_interfaces[0].protocol_version == "1.0"
        assert card.capabilities.streaming and card.capabilities.push_notifications
asyncio.run(main())
from checkedflow.agents.access import LOCAL, Policy
from checkedflow.agents.download import Reader
from checkedflow.artifacts import LocalStore, Access
from checkedflow.core.artifact import Reference
from hashlib import sha256
from io import BytesIO
with TemporaryDirectory(prefix="checkedflow-published-bytes-") as directory:
    root = Path(directory)
    grant = {k: v for k, v in LOCAL.record().items() if k != "expires"}
    grant.update(chain="install", mission="example", roles=["inspect"], actors=[])
    (root / "policy.json").write_text(json.dumps({
        "profile": "checkedflow/access-policy/v1", "grants": [grant]}))
    ref = Reference("sha256", sha256(b"installed").hexdigest(), 9,
                    "text/plain", "evidence", "example", "1" * 64)
    (root / "catalog.json").write_text(json.dumps({
        "profile": "checkedflow/artifact-publication/v1", "chain": "install",
        "mission": "example", "artifacts": [ref.record()]}))
    store = LocalStore(root / "artifacts.sqlite")
    store.put(ref, BytesIO(b"installed"), access=Access(
        "publisher", frozenset({"example"}), frozenset({"write"})))
    reader = Reader(store, root / "catalog.json",
                    Policy(root / "policy.json", "install", "example"))
    assert reader.read(LOCAL, ref.digest) == (ref, b"installed")
    (root / "policy.json").write_text(json.dumps({
        "profile": "checkedflow/access-policy/v1", "grants": []}))
    try:
        reader.read(LOCAL, ref.digest)
    except Failure as error:
        assert error.code == "ACCESS"
    else:
        raise AssertionError("withdrawn publication access must fail")
from checkedflow.telemetry import Recorder, trace
from opentelemetry.trace import NoOpTracerProvider
telemetry = Recorder()
with telemetry.measure("worker.step"):
    pass
trace(telemetry.drain(), NoOpTracerProvider().get_tracer("installed-smoke"))
print("Installed agent extras smoke passed on", sys.version.split()[0])
"""


def run(args: list[str], cwd: Path) -> None:
    subprocess.run(
        args,
        cwd=cwd,
        check=True,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    (wheel,) = args.dist.resolve().glob("*.whl")
    (source,) = args.dist.resolve().glob("*.tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for name in REQUIRED:
            if name not in names:
                raise SystemExit(f"wheel missing: {name}")
        for name in REQUIRED:
            if "/monitoring/" in name and archive.read(name) != (ROOT / "src" / name).read_bytes():
                raise SystemExit("Monitoring wheel resource differs from checked source")
        if not any(name.endswith("/licenses/LICENSE") for name in names):
            raise SystemExit("wheel missing license")
        if any(Path(name).suffix == ".key" or name.endswith(".sqlite3") for name in names):
            raise SystemExit("private deployment material entered wheel")
    with tarfile.open(source) as archive:
        names = archive.getnames()
        for name in REQUIRED:
            if not any(entry.endswith("/src/" + name) for entry in names):
                raise SystemExit(f"sdist missing: {name}")
    with tempfile.TemporaryDirectory(prefix="checkedflow-installed-") as temporary:
        directory = Path(temporary)
        for artifact in (wheel, source):
            environment = directory / artifact.name
            run(["uv", "venv", "--python", args.python, str(environment)], directory)
            executable = environment / (
                "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
            )
            run(["uv", "pip", "install", "--python", str(executable), str(artifact)], directory)
            run([str(executable), "-I", "-c", SMOKE], directory)
            run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(executable),
                    str(artifact) + "[agents,distributed,effects,telemetry]",
                ],
                directory,
            )
            run([str(executable), "-I", "-c", AGENT_SMOKE], directory)
    manifest = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (wheel, source)
    }
    (args.dist / "SHA256SUMS.json").write_text(json.dumps(manifest, sort_keys=True, indent=2))
    print(json.dumps({"python": args.python, "artifacts": manifest}))


if __name__ == "__main__":
    main()
