"""Trusted fixture preparation; all candidate strings stay inert on the host."""

import json
from dataclasses import replace
from hashlib import sha1
from importlib.resources import files

from checkedflow.domains.repository_patch import Contract, Tree, apply_patch, digest_bytes
from checkedflow.repository_execution import CHECKER_DIGEST
from checkedflow.wire import dumps


def invoice(image, source=None):
    data = json.loads(files("checkedflow").joinpath("data/invoice-fixture.json").read_text())
    base = Tree(tuple(sorted((path, value.encode()) for path, value in data["files"].items())))
    replacement = data["replacement"]
    path = replacement["path"]
    content = replacement["content"] if source is None else source
    patch = dumps(
        {
            "version": "repository-patch/v1",
            "changes": [
                {"path": path, "before": digest_bytes(dict(base.files)[path]), "content": content}
            ],
        }
    )
    result = dict(base.files) | {path: content.encode()}
    tree = Tree(tuple(sorted(result.items())))
    cases = dumps(data["inventory"])
    contract = Contract(
        repository=data["repository"],
        base_commit=data["base_commit"],
        base_tree=base.digest,
        result_tree=tree.digest,
        patch_digest=digest_bytes(patch),
        image=image,
        checker_digest=CHECKER_DIGEST,
        test_inventory_digest=digest_bytes(cases),
        lint_config_digest=digest_bytes(b"not-run:independent-output-observation-only"),
        receiver="invoice-fixture",
        allowed_paths=(path,),
        max_changed_files=1,
        max_patch_bytes=8192,
        cpu_seconds=3,
        memory_bytes=67108864,
        output_bytes=8192,
        deadline_height=100,
    )
    return base, patch, contract, cases


def successor(base, patch, contract):
    """A real Git commit object for a content-changed fixture, without invoking Git hooks."""
    addition = ("REBASE.txt", b"A distinct approved fixture base.\n")
    changed = Tree(tuple(sorted((*base.files, addition))))
    result = Tree(tuple(sorted((*apply_patch(base, patch, contract).files, addition))))

    def git_object(kind, body):
        # Git object identity only; security bindings use the separate SHA-256 tree digests.
        return sha1(
            kind.encode() + b" " + str(len(body)).encode() + b"\0" + body, usedforsecurity=False
        ).digest()

    def git_tree(entries):
        grouped = {}
        for path, body in entries:
            head, separator, tail = path.partition("/")
            if separator:
                grouped.setdefault(head, []).append((tail, body))
            else:
                grouped[head] = body
        rows = []
        for name, body in sorted(
            grouped.items(), key=lambda pair: pair[0] + ("/" if isinstance(pair[1], list) else "")
        ):
            directory = isinstance(body, list)
            identity = git_tree(body) if directory else git_object("blob", body)
            rows.append((b"40000 " if directory else b"100644 ") + name.encode() + b"\0" + identity)
        return git_object("tree", b"".join(rows))

    commit = (
        f"tree {git_tree(changed.files).hex()}\nparent {contract.base_commit}\n"
        "author CheckedFlow Fixture <fixture@example.invalid> 1 +0000\n"
        "committer CheckedFlow Fixture <fixture@example.invalid> 1 +0000\n\n"
        "Change the immutable repository fixture base.\n"
    ).encode()
    return (
        changed,
        replace(
            contract,
            base_commit=git_object("commit", commit).hex(),
            base_tree=changed.digest,
            result_tree=result.digest,
        ),
        commit,
    )
