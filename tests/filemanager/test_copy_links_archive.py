"""Copy of links into a ZIP archive (REQ-4, REQ-5) and out of a tar archive (ACT3 risk 5)."""

import asyncio
import zipfile
from pathlib import Path

import pytest

from nova_navigator.archive.backing import LocalArchiveBacking
from nova_navigator.archive.tar_archive import TarArchive
from nova_navigator.filemanager.tasks import copy_files
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.tar_fixtures import build_tar_with_symlink_tree

from .common import make_status, run_task


def _zip_destination(tmp_path: Path) -> tuple[Path, ArchiveFilesystem]:
    archive = tmp_path / "out.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("keep.txt", b"keep")
    local = LocalFilesystem.singleton()
    return archive, ArchiveFilesystem(local.path(tmp_path), LocalArchiveBacking(local.path(archive)), work_dir=tmp_path / "work")


def _names(archive: Path) -> set[str]:
    with zipfile.ZipFile(archive) as z:
        return set(z.namelist()) - {"keep.txt"}


@pytest.mark.asyncio
async def test_copy_tree_with_links_into_zip(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "f.txt").write_text("x")
    (src / "sub" / "g.txt").write_text("g")
    (src / "sub" / "up").symlink_to("..", target_is_directory=True)
    (src / "lf").symlink_to("f.txt")
    (src / "ld").symlink_to("sub", target_is_directory=True)
    (src / "broken").symlink_to("nope")
    archive, afs = _zip_destination(tmp_path)
    local = LocalFilesystem()
    status = make_status()
    await asyncio.wait_for(run_task(lambda ctx: copy_files(ctx, [local.path(src)], afs.path("/")), status=status), timeout=60)
    assert _names(archive) == {"src/f.txt", "src/sub/g.txt", "src/lf", "src/ld/g.txt"}
    with zipfile.ZipFile(archive) as z:
        assert z.read("src/lf") == b"x"
        assert z.read("src/ld/g.txt") == b"g"
    assert sorted(s.reason for s in status.skipped) == ["broken link", "link loop", "link loop"]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_selected_directory_link_into_zip_applies_nested_rule(tmp_path: Path) -> None:
    tgt = tmp_path / "tgt"
    (tgt / "inner").mkdir(parents=True)
    (tgt / "f2.txt").write_text("two")
    (tgt / "inner" / "z.txt").write_text("zed")
    (tgt / "nf").symlink_to("f2.txt")
    (tgt / "nd").symlink_to("inner", target_is_directory=True)
    (tmp_path / "lnk").symlink_to("tgt", target_is_directory=True)
    archive, afs = _zip_destination(tmp_path)
    await run_task(lambda ctx: copy_files(ctx, [LocalFilesystem().path(tmp_path / "lnk")], afs.path("/")))
    assert _names(archive) == {"lnk/f2.txt", "lnk/nf", "lnk/inner/z.txt", "lnk/nd/z.txt"}


@pytest.mark.asyncio
async def test_copy_tar_directory_symlink_member_into_zip_copies_tree(tmp_path: Path) -> None:
    build_tar_with_symlink_tree(tmp_path / "s.tar.gz")
    tfs = ArchiveFilesystem(VPath(tmp_path, LocalFilesystem()), TarArchive(tmp_path / "s.tar.gz", mode="r"))
    archive, afs = _zip_destination(tmp_path)
    status = make_status()
    await run_task(lambda ctx: copy_files(ctx, [tfs.path("/lnk"), tfs.path("/flnk"), tfs.path("/broken")], afs.path("/")), status=status)
    assert _names(archive) == {"lnk/f.txt", "flnk"}
    assert sorted(s.reason for s in status.skipped) == ["broken link", "link loop"]  # real/up -> .. loops inside the copied tree
