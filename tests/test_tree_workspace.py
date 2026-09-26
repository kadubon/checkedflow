"""Filesystem admission checks never execute candidate contents on the host."""

import os
import platform

import pytest

from checkedflow.core.values import Failure
from checkedflow.domains.repository_patch import Tree
from checkedflow.tree_workspace import materialize


def test_workspace_rejects_unsupported_host(tmp_path, monkeypatch):
    monkeypatch.setattr("checkedflow.tree_workspace.platform.system", lambda: "Windows")
    with pytest.raises(Failure, match="SANDBOX_UNAVAILABLE"):
        materialize(tmp_path, Tree((("module.py", b"raise RuntimeError"),)))
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux descriptor filesystem contract")
def test_workspace_exact_bytes_and_no_follow(tmp_path):
    tmp_path.chmod(0o700)
    tree = Tree((("pkg/a.py", b"raise RuntimeError\r\n"), ("pkg/b.py", b"")))
    source = materialize(tmp_path, tree)
    for name, content in tree.files:
        assert (source / name).read_bytes() == content
        assert (source / name).stat().st_mode & 0o777 == 0o444
    assert (source / "pkg").stat().st_mode & 0o777 == 0o755
    with pytest.raises(FileExistsError):
        materialize(tmp_path, tree)
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        materialize(linked, tree)


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux descriptor filesystem contract")
def test_workspace_private_parent_and_preexisting_symlink(tmp_path):
    tree = Tree((("module.py", b""),))
    tmp_path.chmod(0o755)
    with pytest.raises(Failure, match="PATH"):
        materialize(tmp_path, tree)
    tmp_path.chmod(0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "source").symlink_to(outside, target_is_directory=True)
    with pytest.raises(FileExistsError):
        materialize(tmp_path, tree)
    assert not os.listdir(outside)


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux descriptor filesystem contract")
def test_workspace_rejects_replaced_directory(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    original = os.mkdir

    def racing_mkdir(path, mode=0o777, *, dir_fd=None):
        if path == "pkg":
            os.symlink(str(outside), path, dir_fd=dir_fd)
            raise FileExistsError
        return original(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", racing_mkdir)
    with pytest.raises(OSError):
        materialize(tmp_path, Tree((("pkg/escape.py", b"must not be written"),)))
    assert not list(outside.iterdir())
