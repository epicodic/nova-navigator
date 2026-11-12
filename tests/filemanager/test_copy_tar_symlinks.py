"""Tar symlink members copy as symlinks to a link-capable destination (local and SSH)."""

from pathlib import Path

import pytest

from nova_navigator.archive.tar_archive import TarArchive
from nova_navigator.filemanager.tasks import copy_files
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.tar_fixtures import build_tar_with_links, build_tar_with_symlink_tree

from .common import run_task


@pytest.mark.asyncio
async def test_copy_tar_symlink_member_recreates_link(link_fs: Filesystem, tmp_path: Path) -> None:
    build_tar_with_links(tmp_path / "l.tar.gz")
    dest = tmp_path / "out"
    dest.mkdir()
    afs = ArchiveFilesystem(VPath(tmp_path, LocalFilesystem()), TarArchive(tmp_path / "l.tar.gz", mode="r"))
    await run_task(lambda ctx: copy_files(ctx, [afs.path("/s.txt"), afs.path("/b.txt")], link_fs.path(dest)))
    assert (dest / "s.txt").is_symlink()
    assert (dest / "s.txt").readlink() == Path("a.txt")
    assert not (dest / "b.txt").is_symlink()  # hard link members still copy as regular files (ACT3)
    assert (dest / "b.txt").read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_copy_tar_directory_symlink_member_recreates_link(link_fs: Filesystem, tmp_path: Path) -> None:
    build_tar_with_symlink_tree(tmp_path / "s.tar.gz")
    dest = tmp_path / "out"
    dest.mkdir()
    afs = ArchiveFilesystem(VPath(tmp_path, LocalFilesystem()), TarArchive(tmp_path / "s.tar.gz", mode="r"))
    await run_task(lambda ctx: copy_files(ctx, [afs.path("/lnk"), afs.path("/broken"), afs.path("/abs")], link_fs.path(dest)))
    assert (dest / "lnk").readlink() == Path("real")
    assert (dest / "broken").readlink() == Path("nope")
    assert (dest / "abs").readlink() == Path("/etc/passwd")
