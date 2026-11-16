"""SSH integration test: a bare compressed file on the SFTP server mounts as a read-only one-entry archive."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from nova_navigator.archive.backing import CopiedArchiveBacking
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.local_copy import LocalCopy
from tests.integration.ssh.conftest import SshAppCtx


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.ssh
async def test_gz_on_sftp_mounts_and_reads_back(ssh_app_ctx: SshAppCtx, tmp_path: Path) -> None:
    remote_file = ssh_app_ctx.remote_dir / "notes.txt.gz"
    remote_file.write_bytes(gzip.compress(b"remote content"))
    source = VPath(remote_file, ssh_app_ctx.ssh_fs)

    copy = LocalCopy.create(source, tmp_path / "copies", read_only=True)
    archive_fs = ArchiveFilesystem(source.parent, CopiedArchiveBacking(copy), work_dir=tmp_path / "work")

    assert archive_fs.capabilities.read_only
    entries = [entry async for entry in archive_fs.iterdir(archive_fs.root())]
    assert [entry.name for entry in entries] == ["notes.txt"]
    reader = archive_fs.read(entries[0])
    try:
        assert reader.read(1_000_000) == b"remote content"
    finally:
        reader.close()
    with pytest.raises(PermissionError):
        archive_fs.write(entries[0])
