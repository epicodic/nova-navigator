"""Follow links into and out of archives (REQ-3, REQ-5): same results with and without stable symlinks."""

import asyncio
import zipfile
from pathlib import Path

import pytest

from nova_navigator.archive.backing import LocalArchiveBacking
from nova_navigator.archive.tar_archive import TarArchive
from nova_navigator.filemanager.tasks import FileCopyOptions, copy_files
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.tar_fixtures import build_tar_with_symlink_tree

from .common import make_status, reasons, run_task

_OPTIONS = [
    pytest.param(FileCopyOptions(follow_links=True), id="follow"),
    pytest.param(FileCopyOptions(follow_links=True, stable_symlinks=True), id="follow+stable"),
]


def _zip_destination(tmp_path: Path) -> tuple[Path, ArchiveFilesystem]:
    archive = tmp_path / "out.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("keep.txt", b"keep")
    local = LocalFilesystem.singleton()
    return archive, ArchiveFilesystem(
        local.path(tmp_path),
        LocalArchiveBacking(local.path(archive)),
        work_dir=tmp_path / "work",
    )


def _names(archive: Path) -> set[str]:
    with zipfile.ZipFile(archive) as z:
        return set(z.namelist()) - {"keep.txt"}


@pytest.mark.asyncio
@pytest.mark.parametrize("options", _OPTIONS)
async def test_follow_tree_with_links_into_zip_same_as_default(tmp_path: Path, options: FileCopyOptions) -> None:
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
    await asyncio.wait_for(
        run_task(lambda ctx: copy_files(ctx, [local.path(src)], afs.path("/"), options), status=status),
        timeout=60,
    )
    assert _names(archive) == {"src/f.txt", "src/sub/g.txt", "src/lf", "src/ld/g.txt"}
    assert sorted(reasons(status)) == ["broken link", "link loop", "link loop"]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize("options", _OPTIONS)
async def test_follow_selected_dir_link_into_zip(tmp_path: Path, options: FileCopyOptions) -> None:
    tgt = tmp_path / "tgt"
    (tgt / "inner").mkdir(parents=True)
    (tgt / "f2.txt").write_text("two")
    (tgt / "inner" / "z.txt").write_text("zed")
    (tgt / "nf").symlink_to("f2.txt")
    (tgt / "nd").symlink_to("inner", target_is_directory=True)
    (tmp_path / "lnk").symlink_to("tgt", target_is_directory=True)
    archive, afs = _zip_destination(tmp_path)
    await run_task(lambda ctx: copy_files(ctx, [LocalFilesystem().path(tmp_path / "lnk")], afs.path("/"), options))
    assert _names(archive) == {"lnk/f2.txt", "lnk/nf", "lnk/inner/z.txt", "lnk/nd/z.txt"}


@pytest.mark.asyncio
@pytest.mark.parametrize("options", _OPTIONS)
async def test_follow_tar_source_links_to_local(tmp_path: Path, options: FileCopyOptions) -> None:
    build_tar_with_symlink_tree(tmp_path / "s.tar.gz")
    tfs = ArchiveFilesystem(VPath(tmp_path, LocalFilesystem()), TarArchive(tmp_path / "s.tar.gz", mode="r"))
    out = tmp_path / "out"
    out.mkdir()
    status = make_status()
    await run_task(
        lambda ctx: copy_files(
            ctx,
            [tfs.path("/lnk"), tfs.path("/flnk"), tfs.path("/broken")],
            LocalFilesystem().path(out),
            options,
        ),
        status=status,
    )
    assert out.is_dir()
    assert (out / "lnk" / "f.txt").read_text() == "hello"
    assert (out / "flnk").is_file()
    assert not (out / "flnk").is_symlink()
    assert not any(p.is_symlink() for p in out.rglob("*"))
    assert sorted(reasons(status)) == ["broken link", "link loop"]


@pytest.mark.asyncio
async def test_stable_into_zip_has_no_effect_links_are_followed(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "f.txt").write_text("x")
    (src / "sub" / "g.txt").write_text("g")
    (src / "sub" / "up").symlink_to("..", target_is_directory=True)
    (src / "lf").symlink_to("f.txt")
    (src / "ld").symlink_to("sub", target_is_directory=True)
    (src / "broken").symlink_to("nope")
    archive, afs = _zip_destination(tmp_path)
    status = make_status()
    await asyncio.wait_for(
        run_task(
            lambda ctx: copy_files(
                ctx,
                [LocalFilesystem().path(src)],
                afs.path("/"),
                FileCopyOptions(stable_symlinks=True),
            ),
            status=status,
        ),
        timeout=60,
    )
    assert _names(archive) == {"src/f.txt", "src/sub/g.txt", "src/lf", "src/ld/g.txt"}
    assert status.progress.completed == status.progress.total
