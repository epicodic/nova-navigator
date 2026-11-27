"""Saving a copy opened through a symbolic link replaces the link's target and keeps the link (S4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nova_navigator.local_copies.manager import CopyStatus, LocalCopyManager
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
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
