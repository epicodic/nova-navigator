"""Pins ACT3 interim copy behaviour for links; ACT4 replaces these tests deliberately."""

import asyncio
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, copy_files, move_files
from nova_navigator.vfs.filesystems.local import LocalFilesystem

from .common import make_status, run_task


def _tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "f.txt").write_text("x")
    (src / "d" / "g.txt").write_text("g")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    dst = tmp_path / "dst"
    dst.mkdir()
    return src, outside, dst


def _listing(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.mark.asyncio
async def test_copy_dir_with_file_link_copies_regular_file(tmp_path: Path) -> None:
    src, _, dst = _tree(tmp_path)
    (src / "l").symlink_to("f.txt")
    fs = LocalFilesystem()
    status = make_status()
    await asyncio.wait_for(run_task(lambda ctx: copy_files(ctx, [fs.path(src)], fs.path(dst)), status=status), timeout=20)
    assert (dst / "src" / "l").read_bytes() == b"x"
    assert not (dst / "src" / "l").is_symlink()
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_dir_with_dir_link_skips_and_does_not_descend(tmp_path: Path) -> None:
    src, outside, dst = _tree(tmp_path)
    (src / "out").symlink_to(outside, target_is_directory=True)
    before = _listing(outside)
    fs = LocalFilesystem()
    status = make_status()
    await asyncio.wait_for(run_task(lambda ctx: copy_files(ctx, [fs.path(src)], fs.path(dst)), status=status), timeout=20)
    assert not (dst / "src" / "out").exists()
    assert not (dst / "src" / "out").is_symlink()
    assert _listing(outside) == before
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_broken_link_skipped_job_completes(tmp_path: Path) -> None:
    src, _, dst = _tree(tmp_path)
    (src / "b").symlink_to("nope")
    fs = LocalFilesystem()
    status = make_status()
    await asyncio.wait_for(run_task(lambda ctx: copy_files(ctx, [fs.path(src)], fs.path(dst)), status=status), timeout=20)
    assert not (dst / "src" / "b").exists()
    assert not (dst / "src" / "b").is_symlink()
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_tree_with_link_to_ancestor_completes(tmp_path: Path) -> None:
    src, _, dst = _tree(tmp_path)
    (src / "d" / "up").symlink_to("..", target_is_directory=True)
    fs = LocalFilesystem()
    await asyncio.wait_for(run_task(lambda ctx: copy_files(ctx, [fs.path(src)], fs.path(dst))), timeout=20)
    assert (dst / "src" / "d" / "g.txt").exists()
    assert not (dst / "src" / "d" / "up").exists()
    assert not (dst / "src" / "d" / "up").is_symlink()


@pytest.mark.asyncio
async def test_copy_selected_dir_link_skipped(tmp_path: Path) -> None:
    src, outside, dst = _tree(tmp_path)
    (src / "out").symlink_to(outside, target_is_directory=True)
    fs = LocalFilesystem()
    await asyncio.wait_for(run_task(lambda ctx: copy_files(ctx, [fs.path(src / "out")], fs.path(dst))), timeout=20)
    assert list(dst.iterdir()) == []
    await asyncio.wait_for(run_task(lambda ctx: copy_files(ctx, [fs.path(src / "out")], fs.path(dst / "new"))), timeout=20)
    assert not (dst / "new").exists()


@pytest.mark.asyncio
async def test_copy_selected_file_link_copies_regular_file(tmp_path: Path) -> None:
    src, _, dst = _tree(tmp_path)
    (src / "l").symlink_to("f.txt")
    fs = LocalFilesystem()
    await asyncio.wait_for(run_task(lambda ctx: copy_files(ctx, [fs.path(src / "l")], fs.path(dst))), timeout=20)
    assert (dst / "l").read_bytes() == b"x"
    assert not (dst / "l").is_symlink()


@pytest.mark.asyncio
async def test_move_same_device_dir_with_inner_link_renames_link(tmp_path: Path) -> None:
    src, outside, dst = _tree(tmp_path)
    (src / "out").symlink_to(outside, target_is_directory=True)
    (dst / "src").mkdir()
    before = _listing(outside)
    fs = LocalFilesystem()
    options = FileCopyOptions(overwrite="overwrite")
    await asyncio.wait_for(run_task(lambda ctx: move_files(ctx, [fs.path(src)], fs.path(dst), options)), timeout=20)
    assert (dst / "src" / "out").is_symlink()
    assert _listing(outside) == before
