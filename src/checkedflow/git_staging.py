"""Deterministic unsigned commit metadata for approved whole-tree staging.

The fixed timestamp is a serialization constant, not an observation of external time.
Git SHA-1 identifies provider objects; the contract and patch retain SHA-256 identities.
"""

from checkedflow.core.values import Object, require
from checkedflow.domains.repository_patch import Contract, Tree
from checkedflow.git_tree import _object, tree_id
from checkedflow.repository_reuse import contract_digest


def staging_commit(contract: Contract, result: Tree) -> tuple[str, Object]:
    """Return the exact planned head ID and GitHub create-commit payload, without any I/O."""
    require(result.digest == contract.result_tree, "BINDING", "staged result differs from contract")
    tree = tree_id(result)
    message = (
        "CheckedFlow checked patch\n\nContract: "
        + contract_digest(contract)
        + "\nPatch: "
        + contract.patch_digest
        + "\n"
    )
    person = "CheckedFlow <checkedflow@example.invalid> 946684800 +0000"
    body = (
        f"tree {tree}\nparent {contract.base_commit}\nauthor {person}\ncommitter {person}\n\n"
        + message
    ).encode("utf-8")
    author: Object = {
        "name": "CheckedFlow",
        "email": "checkedflow@example.invalid",
        "date": "2000-01-01T00:00:00Z",
    }
    return _object(b"commit", body).hex(), {
        "message": message,
        "tree": tree,
        "parents": [contract.base_commit],
        "author": author,
        "committer": dict(author),
    }
