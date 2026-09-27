"""Stable local deployment and validator locks; no signing-state mutation."""

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from checkedflow.core.values import Failure, require
from checkedflow.distributed.deployment_preflight import protected


@contextmanager
def exclusive(path: Path, owner: int, *, create: bool = False) -> Iterator[None]:
    """Keep stable local inodes locked; never replace or truncate either lock file."""
    import fcntl

    nofollow = getattr(os, "O_NOFOLLOW", None)
    require(isinstance(nofollow, int), "PLATFORM", "nofollow lock opening required")
    protected(path.parent, owners=(0, owner))
    nonblock = int(getattr(os, "O_NONBLOCK", 0))
    require(nonblock != 0, "PLATFORM", "nonblocking lock opening required")
    flags = cast(int, nofollow) | nonblock | (os.O_RDWR | os.O_CREAT if create else os.O_RDONLY)
    descriptor = os.open(path, flags, 0o600)
    try:
        info, current = os.fstat(descriptor), path.lstat()
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == owner
            and info.st_mode & 0o077 == 0
            and info.st_nlink == 1
            and (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino),
            "CUSTODY",
            "protected stable lock inode required",
        )
        flock = getattr(fcntl, "flock", None)
        if not callable(flock):
            raise Failure("PLATFORM", "native flock required")
        try:
            flock(descriptor, getattr(fcntl, "LOCK_EX", 2) | getattr(fcntl, "LOCK_NB", 4))
        except BlockingIOError:
            raise Failure("BUSY", "deployment or validator lock is held") from None
        yield
    finally:
        os.close(descriptor)
