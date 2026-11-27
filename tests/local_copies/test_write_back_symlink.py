"""Saving a copy opened through a symbolic link replaces the link's target and keeps the link (S4)."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from nova_navigator.local_copies.manager import CopyStatus, LocalCopyManager
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.local_copy import LocalCopy
from nova_navigator.vfs.vpath import VPath
from tests._utils.local_copy_helpers import ScriptedRunner


@pytest.mark.asyncio
async def test_save_through_a_symlink_keeps_the_link(gnu_fs: SSHFilesystem, tmp_path: Path) -> None:
    real_dir = tmp_path / "remote" / "real"
    link_dir = tmp_path / "remote" / "links"
    real_dir.mkdir(parents=True)
    link_dir.mkdir(parents=True)
    target = real_dir / "doc.txt"
    target.write_bytes(b"old")
    link = link_dir / "doc.txt"
    link.symlink_to(target)

    manager = LocalCopyManager(tmp_path / "root", ScriptedRunner(), poll_interval=0.05, settle_time=0.1)
    try:
        entry = await manager.open(VPath(str(link), gnu_fs))
        assert entry is not None
        entry.copy.path.write_bytes(b"new")
        await manager.sync_now(entry)
        assert entry.status is CopyStatus.SYNCED, entry.error
        assert link.is_symlink()
        assert os.readlink(link) == str(target)
        assert target.read_bytes() == b"new"

        entry.copy.path.write_bytes(b"newer")
        await manager.sync_now(entry)
        assert entry.status is CopyStatus.SYNCED
        assert target.read_bytes() == b"newer"
    finally:
        await manager.shutdown()


def test_write_back_through_a_dangling_symlink_creates_its_target(gnu_fs: SSHFilesystem, tmp_path: Path) -> None:
    folder = tmp_path / "remote"
    folder.mkdir(parents=True)
    target = folder / "target.txt"
    target.write_bytes(b"old")
    link = folder / "link.txt"
    link.symlink_to(target)
    copy = LocalCopy.create(VPath(str(link), gnu_fs), tmp_path / "root")
    target.unlink()  # the link now dangles
    copy.path.write_bytes(b"created")
    copy.write_back()
    assert link.is_symlink()
    assert target.read_bytes() == b"created"


def test_write_back_through_a_chain_of_symlinks_creates_the_final_target(gnu_fs: SSHFilesystem, tmp_path: Path) -> None:
    folder = tmp_path / "remote"
    folder.mkdir(parents=True)
    final = folder / "final.txt"
    final.write_bytes(b"old")
    middle = folder / "middle.txt"
    middle.symlink_to(final)
    link = folder / "first.txt"
    link.symlink_to(middle)
    copy = LocalCopy.create(VPath(str(link), gnu_fs), tmp_path / "root")
    final.unlink()  # the chain now ends in a missing file
    copy.path.write_bytes(b"created")
    copy.write_back()
    assert link.is_symlink()
    assert middle.is_symlink()
    assert final.read_bytes() == b"created"


def test_write_back_through_a_symlink_loop_raises_eloop_and_writes_nothing(gnu_fs: SSHFilesystem, tmp_path: Path) -> None:
    folder = tmp_path / "remote"
    folder.mkdir(parents=True)
    target = folder / "target.txt"
    target.write_bytes(b"old")
    link = folder / "link.txt"
    link.symlink_to(target)
    copy = LocalCopy.create(VPath(str(link), gnu_fs), tmp_path / "root")
    link.unlink()
    other = folder / "other.txt"
    link.symlink_to(other)
    other.symlink_to(link)  # the link now loops
    copy.path.write_bytes(b"lost")
    with pytest.raises(OSError, match="Too many levels of symbolic links") as exc_info:
        copy.write_back()
    assert exc_info.value.errno == errno.ELOOP
    assert target.read_bytes() == b"old"
