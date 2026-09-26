"""Trusted fixture preparation; all candidate strings stay inert on the host."""

import json
from importlib.resources import files

from checkedflow.domains.repository_patch import Contract, Tree, digest_bytes
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
