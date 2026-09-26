"""Operator-authorized disposable GitHub provider smoke; not full effect qualification.

Stages fixed inert fixture text, opens one owned draft through the adapter, reconciles and closes
it. Requires explicit immutable repository ID and already disabled Actions. Never run in CI with
untrusted source or candidate credentials. The gh credential is used only by this trusted process.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

import checkedflow
from checkedflow.github_drafts import API_VERSION, Drafts, Outcome, Plan, Token


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
    content = "CheckedFlow disposable provider fixture. No executable code.\n"
    tree = api(
        "POST",
        "/git/trees",
        {
            "base_tree": original["tree"]["sha"],
            "tree": [
                {
                    "path": "checkedflow_fixture.txt",
                    "mode": "100644",
                    "type": "blob",
                    "content": content,
                },
            ],
        },
    )["sha"]
    identity = {"name": "CheckedFlow Qualification", "email": "checkedflow@example.invalid"}
    commit = api(
        "POST",
        "/git/commits",
        {
            "message": "CheckedFlow disposable provider fixture",
            "tree": tree,
            "parents": [base],
            "author": identity,
            "committer": identity,
        },
    )["sha"]
    selected = Plan(
        args.repository,
        args.repository_id,
        operation,
        hashlib.sha256(b"operator-approved-provider-smoke").hexdigest(),
        hashlib.sha256(content.encode()).hexdigest(),
        hashlib.sha256(tree.encode()).hexdigest(),
        base_branch,
        base,
        commit,
        tree,
    )
    # These SHA-256 fixture bindings are not actual consensus authorization or patch acceptance.
    (args.reports / "plan.json").write_text(json.dumps(selected.record(), indent=2) + "\n")
    api("POST", "/git/refs", {"ref": "refs/heads/" + selected.branch, "sha": commit})
    token = Token(gh(["auth", "token"]).decode().strip())
    adapter = Drafts(
        args.repository,
        args.repository_id,
        actor,
        token,
        args.reports / "journal.sqlite",
        enabled=True,
    )
    result = adapter.dispatch(selected)
    if result.status != "confirmed":
        raise SystemExit("Draft outcome unknown; preserve journal and reconcile original operation")
    observed = adapter.reconcile(selected)
    if observed != result or adapter.dispatch(selected) != result:
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
    assert adapter.dispatch(selected) == Outcome("confirmed", result.number)
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
        "release_authority": False,
        "installed_package": installed,
        "python": sys.version.split()[0],
        "wheel_sha256": wheel_hash,
    }
    (args.reports / "github-draft.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
