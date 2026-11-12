"""Move must not follow links when it replaces an existing destination."""

import asyncio
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, move_files
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath

from .common import make_status, run_task


@pytest.mark.asyncio
async def test_move_file_replaces_dir_containing_outside_link(tmp_path: Path) -> None:
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
    assert (dst / "a").read_bytes() == b"new"
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
async def test_move_dir_link_same_device_renames_link_only(tmp_path: Path) -> None:
    outside = _outside(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "lnk").symlink_to(outside, target_is_directory=True)
    dst = tmp_path / "dst"
    (dst / "lnk").mkdir(parents=True)
    (dst / "lnk" / "x.txt").write_text("x")
    fs = LocalFilesystem()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "lnk")], fs.path(dst), FileCopyOptions(overwrite="overwrite")))
    assert (dst / "lnk").is_symlink()
    assert (dst / "lnk").resolve() == outside.resolve()
    assert not (src / "lnk").is_symlink()
    _assert_outside_unchanged(outside)


@pytest.mark.asyncio
async def test_move_cross_device_dir_link_skipped_source_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert not (dst / "out").exists()
    assert not (dst / "out").is_symlink()
    assert (src / "s" / "out").is_symlink()
    assert not (src / "s" / "f.txt").exists()
    _assert_outside_unchanged(outside)
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_cross_device_selected_dir_link_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_cross_device(monkeypatch)
    outside = _outside(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "lnk").symlink_to(outside, target_is_directory=True)
    dst = tmp_path / "dst"
    dst.mkdir()
    fs = LocalFilesystem()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "lnk")], fs.path(dst), FileCopyOptions(overwrite="overwrite")))
    assert (src / "lnk").is_symlink()
    assert list(dst.iterdir()) == []
    _assert_outside_unchanged(outside)


@pytest.mark.asyncio
async def test_move_cross_device_file_link_moves_as_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_cross_device(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    (src / "f.txt").write_text("content")
    (src / "l").symlink_to(src / "f.txt")
    dst = tmp_path / "dst"
    dst.mkdir()
    fs = LocalFilesystem()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "l")], fs.path(dst), FileCopyOptions(overwrite="overwrite")))
    assert (dst / "l").read_text() == "content"
    assert not (dst / "l").is_symlink()
    assert not (src / "l").is_symlink()


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
