"""Operator-authorized disposable GitHub provider smoke; not full effect qualification.

Stages fixed inert fixture text, opens one owned draft through the adapter, reconciles and closes
it. Requires explicit immutable repository ID and already disabled Actions. Never run in CI with
untrusted source or candidate credentials. The gh credential is used only by this trusted process.
"""

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

import checkedflow
from checkedflow.domains.repository_patch import Contract, Tree, digest_bytes
from checkedflow.git_staging import staging_commit
from checkedflow.git_tree import tree_id
from checkedflow.github_drafts import API_VERSION, Drafts, Outcome, Plan, Token
from checkedflow.wire import dumps


def gh(arguments, *, payload=None):
    result = subprocess.run(
        ["gh", *arguments],
        input=None if payload is None else json.dumps(payload).encode(),
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise SystemExit("GitHub operation failed; inspect owned fixture state before retrying")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-id", type=int, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--require-installed", action="store_true")
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    installed = Path(checkedflow.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    if args.require_installed and not installed:
        raise SystemExit("Qualification requires an installed package outside the source checkout")
    if args.require_installed and args.wheel is None:
        raise SystemExit("Installed qualification requires the exact supplied wheel")
    wheel_hash = None
    if args.wheel is not None:
        wheel_hash = hashlib.sha256(args.wheel.read_bytes()).hexdigest()
        package = Path(checkedflow.__file__).resolve().parent
        with zipfile.ZipFile(args.wheel) as archive:
            for member in archive.namelist():
                if member.startswith("checkedflow/") and not member.endswith("/"):
                    destination = package.joinpath(*member.split("/")[1:]).resolve()
                    if not destination.is_relative_to(package):
                        raise SystemExit("Unsafe wheel member")
                    if destination.read_bytes() != archive.read(member):
                        raise SystemExit("Imported package differs from supplied wheel")
    # Fixed configuration, never a repository name obtained from candidate or API output.
    Drafts(
        args.repository, args.repository_id, "preflight", Token("unused"), args.reports / "unused"
    )
    prefix = "repos/" + args.repository

    def api(method, suffix, payload=None):
        command = [
            "api",
            "--method",
            method,
            "-H",
            "X-GitHub-Api-Version: " + API_VERSION,
            prefix + suffix,
        ]
        if payload is not None:
            command += ["--input", "-"]
        body = gh(command, payload=payload)
        return json.loads(body) if body else None

    repo = api("GET", "")
    if repo["id"] != args.repository_id or repo["archived"]:
        raise SystemExit("Immutable fixture repository identity differs")
    if api("GET", "/actions/permissions")["enabled"] is not False:
        raise SystemExit("Disposable fixture Actions must already be disabled")
    args.reports.mkdir(mode=0o700, parents=True, exist_ok=False)
    actor = json.loads(gh(["api", "user"]))["login"]
    operation = hashlib.sha256(uuid4().bytes).hexdigest()
    base_branch = repo["default_branch"]
    base = api("GET", "/git/ref/heads/" + base_branch)["object"]["sha"]
    original = api("GET", "/git/commits/" + base)
    # This trusted fixture supports only the known inert one-file disposable baseline.
    # Refuse unexpected files instead of reading arbitrary repository contents.
    baseline = api("GET", "/git/trees/" + original["tree"]["sha"])
    if baseline.get("truncated") or len(baseline["tree"]) != 1:
        raise SystemExit("Fixture requires the complete one-file README baseline")
    entry = baseline["tree"][0]
    if (entry["path"], entry["mode"], entry["type"]) != ("README.md", "100644", "blob"):
        raise SystemExit("Unexpected fixture baseline")
    blob = api("GET", "/git/blobs/" + entry["sha"])
    if blob["encoding"] != "base64" or blob["size"] > 8192:
        raise SystemExit("Fixture baseline exceeds supported content profile")
    source = Tree(
        (("README.md", base64.b64decode(blob["content"].replace("\n", ""), validate=True)),)
    )
    if tree_id(source) != original["tree"]["sha"]:
        raise SystemExit("Fixture base content differs from Git identity")
    content = "# CheckedFlow disposable provider fixture. Comment only; never executed.\n"
    patch = dumps(
        {
            "version": "repository-patch/v1",
            "changes": [
                {"path": "checkedflow_fixture.py", "before": None, "content": content},
            ],
        }
    )
    expected = Tree((*source.files, ("checkedflow_fixture.py", content.encode())))
    # Explicit operator fixture intent, not checker evidence or consensus authorization.
    placeholder = digest_bytes(b"not-executed-provider-binding-fixture")
    contract = Contract(
        args.repository,
        base,
        source.digest,
        expected.digest,
        digest_bytes(patch),
        "python@sha256:" + "0" * 64,
        placeholder,
        placeholder,
        placeholder,
        "operator-fixture",
        ("checkedflow_fixture.py",),
        1,
        8192,
        1,
        16777216,
        4096,
        1,
        allow_draft_pr=True,
    )
    tree = tree_id(expected)
    commit, _ = staging_commit(contract, expected)
    selected = Plan(
        args.repository,
        args.repository_id,
        operation,
        hashlib.sha256(b"operator-approved-provider-smoke").hexdigest(),
        contract.patch_digest,
        contract.result_tree,
        base_branch,
        base,
        commit,
        tree,
    )
    # Exact patch/tree bindings do not establish consensus authority or checker acceptance.
    (args.reports / "plan.json").write_text(json.dumps(selected.record(), indent=2) + "\n")
    token = Token(gh(["auth", "token"]).decode().strip())
    adapter = Drafts(
        args.repository,
        args.repository_id,
        actor,
        token,
        args.reports / "journal.sqlite",
        enabled=True,
    )

    def before_send(plan):
        if plan != selected:
            raise SystemExit("Fixture staging arguments changed")
        # This is explicit operator fixture approval only, never a consensus credential.

    result = adapter.stage_and_dispatch_patch(
        selected, source, patch, contract, before_send=before_send
    )
    if result.status != "confirmed":
        raise SystemExit("Draft outcome unknown; preserve journal and reconcile original operation")
    observed = adapter.reconcile_patch(selected, source, patch, contract)
    if observed != result or adapter.dispatch_patch(selected, source, patch, contract) != result:
        raise SystemExit("Provider observation or retained receipt differs")
    # Explicit operator cleanup, not an implemented runtime compensation state machine.
    row = api("GET", "/pulls/" + str(result.number))
    if adapter._match(selected, row) != result.number:
        raise SystemExit("Owned draft changed; cleanup refused")
    closed = api("PATCH", "/pulls/" + str(result.number), {"state": "closed"})
    if closed["state"] != "closed" or closed["merged"]:
        raise SystemExit("Cleanup not confirmed")
    if api("GET", "/git/ref/heads/" + selected.branch)["object"]["sha"] != commit:
        raise SystemExit("Owned branch moved; branch cleanup refused")
    api("DELETE", "/git/refs/heads/" + selected.branch)
    assert adapter.dispatch_patch(selected, source, patch, contract) == Outcome(
        "confirmed", result.number
    )
    report = {
        "scope": "github-draft-provider-component",
        "result": "PASS",
        "repository_id": args.repository_id,
        "operation": operation,
        "pull_request": "https://github.com/" + args.repository + "/pull/" + str(result.number),
        "closed": True,
        "branch_removed": True,
        "api_version": API_VERSION,
        "credential_profile": "operator-gh-bootstrap-not-qualified-runtime-custody",
        "candidate_execution": False,
        "consensus_authorization": False,
        "complete_source_patch_binding": True,
        "deterministic_staging": True,
        "checker_acceptance": False,
        "release_authority": False,
        "installed_package": installed,
        "python": sys.version.split()[0],
        "wheel_sha256": wheel_hash,
    }
    (args.reports / "github-draft.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
