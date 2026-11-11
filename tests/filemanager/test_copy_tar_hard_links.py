from pathlib import Path

import pytest

from nova_navigator.archive.tar_archive import TarArchive
from nova_navigator.filemanager.tasks import copy_files
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.tar_fixtures import build_tar_with_links

from .common import run_task


@pytest.mark.asyncio
async def test_copy_tar_hard_link_to_local_dir(tmp_path: Path) -> None:
    archive_path = tmp_path / "l.tar.gz"
    build_tar_with_links(archive_path)
    local = LocalFilesystem()
    dest = tmp_path / "out"
    dest.mkdir()
    afs = ArchiveFilesystem(VPath(tmp_path, local), TarArchive(archive_path, mode="r"))
    await run_task(lambda ctx: copy_files(ctx, [afs.path("/a.txt"), afs.path("/b.txt")], local.path(dest)))
    assert (dest / "a.txt").read_bytes() == b"hello"
    assert (dest / "b.txt").read_bytes() == b"hello"
    assert not (dest / "a.txt").is_symlink()
    assert not (dest / "b.txt").is_symlink()
    assert (dest / "a.txt").stat().st_ino != (dest / "b.txt").stat().st_ino
