"""Move must not follow links when it replaces an existing destination."""

import asyncio
import os
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, move_files
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem

from .common import make_status, read_all, run_task


@pytest.mark.asyncio
async def test_move_file_over_dir_containing_outside_link_keeps_dir(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a").write_bytes(b"new")
    dst = tmp_path / "dst"
    (dst / "a").mkdir(parents=True)
    (dst / "a" / "out").symlink_to(outside, target_is_directory=True)
    fs = LocalFilesystem()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "a")], fs.path(dst), FileCopyOptions(overwrite="overwrite")))
    # A file never replaces a folder (REQ-32): the folder, its link and the link target stay, the source stays.
    assert (dst / "a" / "out").is_symlink()
    assert (src / "a").read_bytes() == b"new"
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]
    assert (outside / "keep.txt").read_text() == "keep"


def _force_cross_device(monkeypatch: pytest.MonkeyPatch) -> None:
    def is_same_device(_self: LocalFilesystem, _a: VPath, _b: VPath) -> bool:
        return False

    monkeypatch.setattr(LocalFilesystem, "is_same_device", is_same_device)


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    return outside


def _assert_outside_unchanged(outside: Path) -> None:
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]


@pytest.mark.asyncio
async def test_move_onto_dest_link_to_dir_removes_only_link(tmp_path: Path) -> None:
    outside = _outside(tmp_path)
    src = tmp_path / "src"
    (src / "s").mkdir(parents=True)
    (src / "s" / "f.txt").write_text("f")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "s").symlink_to(outside, target_is_directory=True)
    fs = LocalFilesystem()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "s")], fs.path(dst), FileCopyOptions(overwrite="overwrite")))
    assert (dst / "s").is_dir()
    assert not (dst / "s").is_symlink()
    assert (dst / "s" / "f.txt").exists()
    _assert_outside_unchanged(outside)


@pytest.mark.asyncio
async def test_move_dir_link_over_real_dir_same_device_is_skipped(tmp_path: Path) -> None:
    outside = _outside(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "lnk").symlink_to(outside, target_is_directory=True)
    dst = tmp_path / "dst"
    (dst / "lnk").mkdir(parents=True)
    (dst / "lnk" / "x.txt").write_text("x")
    fs = LocalFilesystem()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "lnk")], fs.path(dst), FileCopyOptions(overwrite="overwrite")))
    # A link never replaces a real folder (REQ-32): the folder stays, the source link stays.
    assert not (dst / "lnk").is_symlink()
    assert (dst / "lnk" / "x.txt").read_text() == "x"
    assert (src / "lnk").is_symlink()
    _assert_outside_unchanged(outside)


@pytest.mark.asyncio
async def test_move_cross_device_dir_with_outside_link_recreates_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_cross_device(monkeypatch)
    outside = _outside(tmp_path)
    src = tmp_path / "src"
    (src / "s").mkdir(parents=True)
    (src / "s" / "f.txt").write_text("f")
    (src / "s" / "out").symlink_to(outside, target_is_directory=True)
    dst = tmp_path / "dst"
    fs = LocalFilesystem()
    status = make_status()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "s")], fs.path(dst), FileCopyOptions(overwrite="overwrite")), status=status)
    assert (dst / "f.txt").read_text() == "f"
    assert os.readlink(dst / "out") == str(outside)
    assert not (src / "s").exists()
    _assert_outside_unchanged(outside)
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_cross_device_selected_dir_link_recreated_and_source_link_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_cross_device(monkeypatch)
    outside = _outside(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "lnk").symlink_to(outside, target_is_directory=True)
    dst = tmp_path / "dst"
    dst.mkdir()
    fs = LocalFilesystem()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "lnk")], fs.path(dst), FileCopyOptions(overwrite="overwrite")))
    assert os.readlink(dst / "lnk") == str(outside)
    assert not (src / "lnk").is_symlink()
    _assert_outside_unchanged(outside)


@pytest.mark.asyncio
async def test_move_cross_device_file_link_moves_as_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_cross_device(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    (src / "f.txt").write_text("content")
    (src / "l").symlink_to("f.txt")
    dst = tmp_path / "dst"
    dst.mkdir()
    fs = LocalFilesystem()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "l")], fs.path(dst), FileCopyOptions(overwrite="overwrite")))
    assert os.readlink(dst / "l") == "f.txt"
    assert not (src / "l").is_symlink()
    assert (src / "f.txt").read_text() == "content"


@pytest.mark.asyncio
async def test_move_cross_device_skipped_link_stays_at_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_cross_device(monkeypatch)
    src = tmp_path / "src"
    (src / "s").mkdir(parents=True)
    (src / "s" / "f.txt").write_text("f")
    (src / "s" / "broken").symlink_to("nope")
    fs = LocalFilesystem()
    dst_fs = MockFilesystem(symlinks=False)
    status = make_status()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "s")], dst_fs.path("/home/user/dst"), FileCopyOptions(overwrite="overwrite")), status=status)
    assert read_all(dst_fs, "/home/user/dst/f.txt") == b"f"
    assert not dst_fs.exists("/home/user/dst/broken")
    assert (src / "s" / "broken").is_symlink()  # skipped link stays
    assert not (src / "s" / "f.txt").exists()
    assert [(s.item, s.reason) for s in status.skipped] == [(fs.path(src / "s" / "broken").uri, "broken link")]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_cross_device_dir_link_to_nonlink_destination_copies_tree_and_removes_only_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_cross_device(monkeypatch)
    outside = _outside(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "lnk").symlink_to(outside, target_is_directory=True)
    fs = LocalFilesystem()
    dst_fs = MockFilesystem(symlinks=False)
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "lnk")], dst_fs.path("/home/user"), FileCopyOptions(overwrite="overwrite")))
    assert read_all(dst_fs, "/home/user/lnk/keep.txt") == b"keep"
    assert not (src / "lnk").is_symlink()
    _assert_outside_unchanged(outside)


@pytest.mark.asyncio
async def test_move_cross_device_declined_overwrite_keeps_source_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_cross_device(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    (src / "l").symlink_to("f.txt")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "l").write_text("old")
    fs = LocalFilesystem()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "l")], fs.path(dst), FileCopyOptions(overwrite="skip")))
    assert (src / "l").is_symlink()
    assert (dst / "l").read_text() == "old"


@pytest.mark.asyncio
async def test_move_local_to_ssh_dir_with_outside_link_recreates_link(link_fs: Filesystem, tmp_path: Path) -> None:
    outside = _outside(tmp_path)
    src = tmp_path / "src"
    (src / "s").mkdir(parents=True)
    (src / "s" / "f.txt").write_text("f")
    (src / "s" / "out").symlink_to(outside, target_is_directory=True)
    dst = tmp_path / "dst"
    dst.mkdir()
    local = LocalFilesystem()
    await run_task(lambda ctx: move_files(ctx, [local.path(src / "s")], link_fs.path(dst), FileCopyOptions(overwrite="overwrite")))
    assert os.readlink(dst / "s" / "out") == str(outside)
    assert not (src / "s").exists()
    _assert_outside_unchanged(outside)


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
