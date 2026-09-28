"""SSH integration test: writable archive mounted from a remote (SFTP) copy.

Mirrors ``test_ssh_write_atomic.py``: exercises ``CopiedArchiveBacking``/``ArchiveFilesystem``
directly against the real SFTP stack, since there is no dedicated archive-edit keybinding.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from nova_navigator.archive.backing import CopiedArchiveBacking
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.local_copy import LocalCopy
from tests.integration.ssh.conftest import SshAppCtx


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.ssh
async def test_write_member_through_remote_archive_mount(ssh_app_ctx: SshAppCtx, tmp_path: Path) -> None:
    """Mount a zip copied from SFTP, write a member, and verify both members over SFTP."""
    archive_path = ssh_app_ctx.remote_dir / "a.zip"
    archive_path.write_bytes(_zip_bytes({"dir/edit.txt": b"old", "keep.txt": b"keep"}))
    zip_vpath = VPath(archive_path, ssh_app_ctx.ssh_fs)

    copy = LocalCopy.create(zip_vpath, tmp_path / "copies")
    backing = CopiedArchiveBacking(copy)
    archive_fs = ArchiveFilesystem(zip_vpath.parent, backing, work_dir=tmp_path / "work")

    writer = archive_fs.write(archive_fs.path("/dir/edit.txt"))
    writer.write(b"new")
    writer.close()

    # Download the rebuilt archive back over SFTP (independent of the local copy) and verify
    # both the edited member and the untouched one.
    downloaded = io.BytesIO()
    reader = ssh_app_ctx.ssh_fs.read(zip_vpath)
    try:
        while chunk := reader.read(65536):
            downloaded.write(chunk)
    finally:
        reader.close()
    with zipfile.ZipFile(downloaded) as z:
        assert z.read("dir/edit.txt") == b"new"
        assert z.read("keep.txt") == b"keep"

    names = {item.name for item in ssh_app_ctx.remote_dir.iterdir()}
    assert not any(name.endswith(".nn-tmp") for name in names)
