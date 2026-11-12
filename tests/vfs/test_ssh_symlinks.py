"""SSHFilesystem symlink API against the stub SSH server (real shell, real files)."""

from __future__ import annotations

import os
from pathlib import Path

from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath


def test_ssh_supports_symlinks_and_agrees_with_capabilities(ssh_stub_fs: SSHFilesystem) -> None:
    assert ssh_stub_fs.supports_symlinks() is True
    assert ssh_stub_fs.capabilities.symlinks is True


def test_ssh_symlink_creates_link_with_identical_text(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    link = VPath(tmp_path / "l", ssh_stub_fs)
    ssh_stub_fs.symlink(link, "../some/relative")
    assert os.readlink(tmp_path / "l") == "../some/relative"
    assert ssh_stub_fs.readlink(link) == "../some/relative"


def test_ssh_symlink_refreshes_parent_listing(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x")
    before = ssh_stub_fs.stat(VPath(tmp_path / "f.txt", ssh_stub_fs))
    assert before.is_symlink is False
    ssh_stub_fs.symlink(VPath(tmp_path / "l", ssh_stub_fs), "f.txt")
    after = ssh_stub_fs.stat(VPath(tmp_path / "l", ssh_stub_fs))
    assert after.is_symlink is True
