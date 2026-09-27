"""Linux-only no-follow materialization into an operator-owned private directory."""

import os
import platform
import sys
from contextlib import suppress
from pathlib import Path

from checkedflow.core.values import require
from checkedflow.domains.repository_patch import Tree


def materialize(parent: Path, tree: Tree) -> Path:
    """Create a fresh read-only source tree, without interpreting its contents.

    The caller owns an inaccessible temporary parent and its cleanup. This is not an
    extractor into a shared directory; same-UID processes and the host remain trusted.
    File descriptors pin directories and reject symlinks at every traversed component.
    """
    require(platform.system() == "Linux", "SANDBOX_UNAVAILABLE", "Linux workspace required")
    if sys.platform == "win32":
        raise RuntimeError("Linux descriptor operations are unavailable")
    # Revalidate even if a caller bypassed the frozen dataclass constructor.
    checked = Tree(tree.files)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_fd = os.open(parent, flags)
    try:
        require(os.fstat(parent_fd).st_mode & 0o077 == 0, "PATH", "private parent required")
        os.mkdir("source", mode=0o755, dir_fd=parent_fd)
        root_fd = os.open("source", flags, dir_fd=parent_fd)
        try:
            os.fchmod(root_fd, 0o755)  # nosec B103: nonroot sandbox traversal; private parent
            for name, content in checked.files:
                parts = name.split("/")
                directory = os.dup(root_fd)
                try:
                    for part in parts[:-1]:
                        with suppress(FileExistsError):
                            os.mkdir(part, mode=0o755, dir_fd=directory)
                        child = os.open(part, flags, dir_fd=directory)
                        os.close(directory)
                        directory = child
                        os.fchmod(directory, 0o755)  # nosec B103: nonroot sandbox traversal
                    file_fd = os.open(
                        parts[-1],
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=directory,
                    )
                    with os.fdopen(file_fd, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fchmod(stream.fileno(), 0o444)
                finally:
                    os.close(directory)
        finally:
            os.close(root_fd)
    finally:
        os.close(parent_fd)
    return parent / "source"
