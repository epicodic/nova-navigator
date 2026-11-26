"""Follow links (REQ-3): copy what a link points to on every destination; skip broken and looping links."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, _copy_link, copy_files
from nova_navigator.scheduler import TaskContext
from nova_navigator.vfs.filesystem import Filesystem, StreamWriterLike
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem

from .common import copy_between, make_status, reasons, run_task

_FOLLOW = FileCopyOptions(follow_links=True)


def _build(tmp_path: Path) -> tuple[Path, Path]:
    """src: f.txt='x', sub/g.txt='g', file_link->f.txt, dir_link->sub, broken->nope; returns (src, dst)."""
    (tmp_path / "src" / "sub").mkdir(parents=True)
    (tmp_path / "src" / "f.txt").write_text("x")
    (tmp_path / "src" / "sub" / "g.txt").write_text("g")
    (tmp_path / "src" / "file_link").symlink_to("f.txt")
    (tmp_path / "src" / "dir_link").symlink_to("sub", target_is_directory=True)
    (tmp_path / "src" / "broken").symlink_to("nope")
    (tmp_path / "dst").mkdir()
    return tmp_path / "src", tmp_path / "dst"


@pytest.mark.asyncio
async def test_follow_file_link_copies_content_no_link_created(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
) -> None:
    src, dst = _build(tmp_path)
    status = await copy_between(fs_pair, [src / "file_link"], dst, _FOLLOW)
    copied = dst / "file_link"
    assert copied.is_file()
    assert not copied.is_symlink()
    assert copied.read_text() == "x"
    assert reasons(status) == []
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_follow_dir_link_copies_tree_under_link_name(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
) -> None:
    src, dst = _build(tmp_path)
    status = await copy_between(fs_pair, [src / "dir_link"], dst, _FOLLOW)
    copied = dst / "dir_link"
    assert copied.is_dir()
    assert not copied.is_symlink()
    assert (copied / "g.txt").read_text() == "g"
    assert reasons(status) == []
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_follow_broken_link_creates_nothing_and_reports(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
) -> None:
    src, dst = _build(tmp_path)
    status = await copy_between(fs_pair, [src / "broken"], dst, _FOLLOW)
    assert not os.path.lexists(dst / "broken")
    assert reasons(status) == ["broken link"]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_follow_self_cycle_reported_as_broken(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path) -> None:
    (tmp_path / "src2").mkdir()
    (tmp_path / "src2" / "l").symlink_to("l")
    (tmp_path / "dst").mkdir()
    status = await copy_between(fs_pair, [tmp_path / "src2" / "l"], tmp_path / "dst", _FOLLOW)
    assert not os.path.lexists(tmp_path / "dst" / "l")
    assert reasons(status) == ["broken link"]


@pytest.mark.asyncio
async def test_follow_link_to_holding_directory_is_loop(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path) -> None:
    (tmp_path / "src" / "d").mkdir(parents=True)
    (tmp_path / "src" / "d" / "g.txt").write_text("g")
    (tmp_path / "src" / "d" / "self").symlink_to(".", target_is_directory=True)
    (tmp_path / "dst").mkdir()
    status = await copy_between(fs_pair, [tmp_path / "src"], tmp_path / "dst", _FOLLOW)
    assert (tmp_path / "dst" / "src" / "d" / "g.txt").read_text() == "g"
    assert not os.path.lexists(tmp_path / "dst" / "src" / "d" / "self")
    assert reasons(status) == ["link loop"]


@pytest.mark.asyncio
async def test_follow_link_to_ancestor_is_loop_and_rest_completes(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "d").mkdir(parents=True)
    (tmp_path / "src" / "f.txt").write_text("f")
    (tmp_path / "src" / "d" / "up").symlink_to("..", target_is_directory=True)
    (tmp_path / "dst").mkdir()
    status = await copy_between(fs_pair, [tmp_path / "src"], tmp_path / "dst", _FOLLOW)
    assert (tmp_path / "dst" / "src" / "f.txt").read_text() == "f"
    assert not os.path.lexists(tmp_path / "dst" / "src" / "d" / "up")
    assert reasons(status) == ["link loop"]


@pytest.mark.asyncio
async def test_follow_link_to_directory_being_copied_higher_up_is_loop(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
) -> None:
    for name in ("a", "b"):
        (tmp_path / "src" / name).mkdir(parents=True)
        (tmp_path / "src" / name / f"{name}.txt").write_text(name)
    (tmp_path / "src" / "a" / "lnk").symlink_to("../b", target_is_directory=True)
    (tmp_path / "src" / "b" / "back").symlink_to("../a", target_is_directory=True)
    (tmp_path / "dst").mkdir()
    status = await copy_between(fs_pair, [tmp_path / "src"], tmp_path / "dst", _FOLLOW)
    dst = tmp_path / "dst" / "src"
    assert (dst / "a" / "lnk" / "b.txt").read_text() == "b"
    assert (dst / "b" / "back" / "a.txt").read_text() == "a"
    assert not os.path.lexists(dst / "a" / "lnk" / "back")
    assert not os.path.lexists(dst / "b" / "back" / "lnk")
    assert reasons(status) == ["link loop", "link loop"]


@pytest.mark.asyncio
async def test_follow_two_links_to_same_directory_both_copied(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "b").mkdir(parents=True)
    (tmp_path / "src" / "b" / "f.txt").write_text("f")
    (tmp_path / "src" / "x").symlink_to("b", target_is_directory=True)
    (tmp_path / "src" / "y").symlink_to("b", target_is_directory=True)
    (tmp_path / "dst").mkdir()
    status = await copy_between(fs_pair, [tmp_path / "src"], tmp_path / "dst", _FOLLOW)
    assert (tmp_path / "dst" / "src" / "x" / "f.txt").read_text() == "f"
    assert (tmp_path / "dst" / "src" / "y" / "f.txt").read_text() == "f"
    assert reasons(status) == []


@pytest.mark.asyncio
async def test_follow_inner_links_of_followed_tree_are_followed(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "tree").mkdir(parents=True)
    (tmp_path / "src" / "f.txt").write_text("x")
    (tmp_path / "src" / "tree" / "h.txt").write_text("h")
    (tmp_path / "src" / "tree" / "in_link").symlink_to("../f.txt")
    (tmp_path / "src" / "dir_link").symlink_to("tree", target_is_directory=True)
    (tmp_path / "dst").mkdir()
    await copy_between(fs_pair, [tmp_path / "src" / "dir_link"], tmp_path / "dst", _FOLLOW)
    copied_link = tmp_path / "dst" / "dir_link" / "in_link"
    assert copied_link.is_file()
    assert not copied_link.is_symlink()
    assert copied_link.read_text() == "x"
    assert (tmp_path / "dst" / "dir_link" / "h.txt").exists()


@pytest.mark.asyncio
async def test_follow_inner_broken_and_loop_reported(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path) -> None:
    (tmp_path / "src" / "tree").mkdir(parents=True)
    (tmp_path / "src" / "tree" / "bad").symlink_to("nope")
    (tmp_path / "src" / "tree" / "up").symlink_to("..", target_is_directory=True)
    (tmp_path / "src" / "dir_link").symlink_to("tree", target_is_directory=True)
    (tmp_path / "dst").mkdir()
    status = await copy_between(fs_pair, [tmp_path / "src" / "dir_link"], tmp_path / "dst", _FOLLOW)
    assert sorted(reasons(status)) == ["broken link", "link loop"]
    assert not os.path.lexists(tmp_path / "dst" / "dir_link" / "bad")
    assert not os.path.lexists(tmp_path / "dst" / "dir_link" / "up")


@pytest.mark.asyncio
async def test_follow_selected_folder_with_links_follows_all(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
) -> None:
    src, dst = _build(tmp_path)
    status = await copy_between(fs_pair, [src], dst, _FOLLOW)
    copied = dst / "src"
    assert not any(p.is_symlink() for p in copied.rglob("*"))
    assert (copied / "file_link").read_text() == "x"
    assert (copied / "dir_link" / "g.txt").exists()
    assert reasons(status) == ["broken link"]


@pytest.mark.asyncio
async def test_follow_loop_depth_guard_when_real_path_unavailable(tmp_path: Path) -> None:
    class _NoRealPathFs(MockFilesystem):
        def real_path(self, path: VPath) -> VPath | None:
            return None

    src_fs = _NoRealPathFs({"/src/d/g.txt": b"g"}, links={"/src/d/up": ".."})
    dst = tmp_path / "dst"
    dst.mkdir()
    status = make_status()
    await asyncio.wait_for(
        run_task(
            lambda ctx: copy_files(ctx, [src_fs.path("/src")], LocalFilesystem().path(dst), _FOLLOW),
            status=status,
        ),
        timeout=20,
    )
    assert (dst / "src" / "d" / "g.txt").exists()
    assert reasons(status) == ["link loop"]


@pytest.mark.asyncio
async def test_follow_into_existing_folder_merges_via_ensure_directory(tmp_path: Path) -> None:
    src, dst = _build(tmp_path)
    (dst / "dir_link").mkdir()
    (dst / "dir_link" / "keep.txt").write_text("keep")
    local = LocalFilesystem()
    await copy_between((local, local), [src / "dir_link"], dst, _FOLLOW)
    assert (dst / "dir_link" / "keep.txt").read_text() == "keep"
    assert (dst / "dir_link" / "g.txt").read_text() == "g"


@pytest.mark.asyncio
async def test_follow_dir_link_onto_existing_destination_link_replaces_link_not_target(tmp_path: Path) -> None:
    src, dst = _build(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (dst / "dir_link").symlink_to(outside, target_is_directory=True)
    local = LocalFilesystem()
    await copy_between(
        (local, local),
        [src / "dir_link"],
        dst,
        FileCopyOptions(follow_links=True, overwrite="overwrite"),
    )
    assert (dst / "dir_link").is_dir()
    assert not (dst / "dir_link").is_symlink()
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]


@pytest.mark.asyncio
async def test_follow_file_link_target_vanished_is_skipped_as_broken(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "dst").mkdir()
    (tmp_path / "src" / "t.txt").write_text("t")
    (tmp_path / "src" / "l").symlink_to("t.txt")
    fs = LocalFilesystem()
    link = fs.path(tmp_path / "src" / "l")
    _ = link.stat  # the cached stat still says "file link"
    os.remove(tmp_path / "src" / "t.txt")
    status = make_status()
    results: list[bool] = []

    async def task(ctx: TaskContext) -> None:
        results.append(await _copy_link(ctx, link, fs.path(tmp_path / "dst" / "l"), _FOLLOW, source_root=None))

    await run_task(task, status=status)
    assert results == [False]
    assert not os.path.lexists(tmp_path / "dst" / "l")
    assert reasons(status) == ["broken link"]


@pytest.mark.asyncio
async def test_follow_dir_link_target_vanished_is_skipped_as_broken(tmp_path: Path) -> None:
    (tmp_path / "src" / "d").mkdir(parents=True)
    (tmp_path / "src" / "d" / "g.txt").write_text("g")
    (tmp_path / "src" / "ld").symlink_to("d", target_is_directory=True)
    (tmp_path / "dst").mkdir()
    fs = LocalFilesystem()
    link = fs.path(tmp_path / "src" / "ld")
    _ = link.stat
    shutil.rmtree(tmp_path / "src" / "d")
    status = make_status()
    results: list[bool] = []

    async def task(ctx: TaskContext) -> None:
        results.append(await _copy_link(ctx, link, fs.path(tmp_path / "dst" / "ld"), _FOLLOW, source_root=None))

    await run_task(task, status=status)
    assert results == [False]
    assert not os.path.lexists(tmp_path / "dst" / "ld")
    assert reasons(status) == ["broken link"]


@pytest.mark.asyncio
async def test_follow_unrelated_file_not_found_is_not_swallowed(tmp_path: Path) -> None:
    """A FileNotFoundError while the target still exists is not a vanished target: it propagates."""

    class _WriteMissingFs(LocalFilesystem):
        def write(self, path: VPath) -> StreamWriterLike:
            raise FileNotFoundError(path.path)

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "t.txt").write_text("t")
    (tmp_path / "src" / "l").symlink_to("t.txt")
    local = LocalFilesystem()
    dst_fs = _WriteMissingFs()
    with pytest.raises(FileNotFoundError):
        await copy_between((local, dst_fs), [tmp_path / "src" / "l"], tmp_path / "dst" / "l", _FOLLOW)
    assert (tmp_path / "src" / "t.txt").exists()


@pytest.mark.asyncio
async def test_follow_file_copy_keeps_target_mode(tmp_path: Path) -> None:
    src, dst = _build(tmp_path)
    os.chmod(src / "f.txt", 0o600)
    local = LocalFilesystem.singleton()
    await copy_between((local, local), [src / "file_link"], dst, _FOLLOW)
    assert os.stat(dst / "file_link").st_mode & 0o777 == 0o600
