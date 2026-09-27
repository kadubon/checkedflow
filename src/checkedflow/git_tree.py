"""Git SHA-1 compatibility for bounded immutable repository-patch trees.

No Git process, checkout, filter, filesystem or network is involved. Application
content identities remain SHA-256; this encoding only matches GitHub object IDs.
"""

from hashlib import sha1

from checkedflow.domains.repository_patch import Tree


def _object(kind: bytes, body: bytes) -> bytes:
    return sha1(
        kind + b" " + str(len(body)).encode("ascii") + b"\x00" + body, usedforsecurity=False
    ).digest()


def tree_id(tree: Tree) -> str:
    """Encode every file as 100644 and every directory as 40000, without normalization.

    The supplied tree must cover the entire repository: omitted files, executable
    bits, symlinks and submodules produce a different remote tree and are rejected.
    """
    directories: dict[str, list[tuple[bytes, bytes, bytes]]] = {"": []}
    for path, content in tree.files:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            directories.setdefault("/".join(parts[:depth]), [])
        parent = "/".join(parts[:-1])
        directories[parent].append(
            (parts[-1].encode("ascii"), b"100644", _object(b"blob", content))
        )
    result = b""
    for path in sorted(directories, key=lambda name: (name.count("/"), len(name)), reverse=True):
        entries = directories[path]
        entries.sort(key=lambda item: item[0] + (b"/" if item[1] == b"40000" else b""))
        result = _object(
            b"tree", b"".join(mode + b" " + name + b"\x00" + oid for name, mode, oid in entries)
        )
        if path:
            parent, _, name = path.rpartition("/")
            directories[parent].append((name.encode("ascii"), b"40000", result))
    return result.hex()
