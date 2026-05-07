"""Tests for LocalFilesystem covering the previously uncovered operations."""

from __future__ import annotations

import os
import stat as stat_mod
from pathlib import Path

import pytest

from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.types import Stat
from tests._utils.mock_filesystem import MockFilesystem

# ── helpers ───────────────────────────────────────────────────────────────────


def _fs() -> LocalFilesystem:
    return LocalFilesystem()


# ── identity / singleton ──────────────────────────────────────────────────────


def test_eq_two_instances_are_equal() -> None:
    assert LocalFilesystem() == LocalFilesystem()


def test_eq_non_instance_is_not_equal() -> None:
    assert LocalFilesystem() != "not a filesystem"


def test_hash_is_stable() -> None:
    assert hash(LocalFilesystem()) == hash(LocalFilesystem())


def test_singleton_returns_same_object() -> None:
    assert LocalFilesystem.singleton() is LocalFilesystem.singleton()


def test_repr() -> None:
    assert repr(LocalFilesystem()) == "LocalFilesystem"


# ── navigation ────────────────────────────────────────────────────────────────


def test_cwd_returns_current_working_directory() -> None:
    fs = _fs()
    assert str(fs.cwd().path) == os.getcwd()


def test_root_returns_slash() -> None:
    fs = _fs()
    assert str(fs.root().path) == "/"


def test_home_returns_home_directory() -> None:
    fs = _fs()
    assert str(fs.home().path) == os.path.expanduser("~")


@pytest.mark.asyncio
async def test_iterdir_lists_contents(tmp_path: Path) -> None:
    fs = _fs()
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    names = sorted([vp.name async for vp in fs.iterdir(fs.path(tmp_path))])
    assert names == ["a.txt", "b.txt"]


def test_parent_returns_parent_path(tmp_path: Path) -> None:
    fs = _fs()
    vp = fs.path(tmp_path / "sub" / "file.txt")
    assert str(fs.parent(vp).path) == str(tmp_path / "sub")


# ── is_same_device ────────────────────────────────────────────────────────────


def test_is_same_device_true_for_same_directory(tmp_path: Path) -> None:
    fs = _fs()
    a_file = tmp_path / "a.txt"
    b_file = tmp_path / "b.txt"
    a_file.write_text("a")
    b_file.write_text("b")
    assert fs.is_same_device(fs.path(a_file), fs.path(b_file)) is True


def test_is_same_device_false_for_different_filesystem_type(tmp_path: Path) -> None:
    local_fs = _fs()
    mock_fs = MockFilesystem({"/x": b""})
    f = tmp_path / "file.txt"
    f.write_text("x")
    path1 = local_fs.path(f)
    path2 = mock_fs.path("/x")
    assert local_fs.is_same_device(path1, path2) is False


def test_is_same_device_false_when_file_missing(tmp_path: Path) -> None:
    fs = _fs()
    a = fs.path(tmp_path / "missing.txt")
    b = fs.path(tmp_path / "also_missing.txt")
    assert fs.is_same_device(a, b) is False


# ── stat ──────────────────────────────────────────────────────────────────────


def test_stat_regular_file(tmp_path: Path) -> None:
    fs = _fs()
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello")
    vp = fs.path(f)
    s = fs.stat(vp)
    assert s.size == 5
    assert s.is_directory is False
    assert s.is_symlink is False
    assert s.is_broken_symlink is False


def test_stat_directory(tmp_path: Path) -> None:
    fs = _fs()
    s = fs.stat(fs.path(tmp_path))
    assert s.is_directory is True


def test_stat_hidden_file(tmp_path: Path) -> None:
    fs = _fs()
    f = tmp_path / ".hidden"
    f.write_text("x")
    s = fs.stat(fs.path(f))
    assert s.is_hidden is True


def test_stat_executable_file(tmp_path: Path) -> None:
    fs = _fs()
    f = tmp_path / "script.sh"
    f.write_text("#!/bin/sh")
    f.chmod(0o755)
    s = fs.stat(fs.path(f))
    assert s.is_executable is True


def test_stat_symlink(tmp_path: Path) -> None:
    fs = _fs()
    target = tmp_path / "target.txt"
    target.write_text("data")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    s = fs.stat(fs.path(link))
    assert s.is_symlink is True
    assert s.is_broken_symlink is False


def test_stat_broken_symlink(tmp_path: Path) -> None:
    fs = _fs()
    link = tmp_path / "broken.txt"
    link.symlink_to(tmp_path / "nonexistent.txt")
    s = fs.stat(fs.path(link))
    assert s.is_symlink is True
    assert s.is_broken_symlink is True


# ── read ──────────────────────────────────────────────────────────────────────


def test_read_returns_file_content(tmp_path: Path) -> None:
    fs = _fs()
    f = tmp_path / "data.bin"
    f.write_bytes(b"\x00\x01\x02\x03")
    vp = fs.path(f)
    reader = fs.read(vp)
    try:
        data = reader.read(4)
        assert data == b"\x00\x01\x02\x03"
    finally:
        reader.close()


def test_read_partial_reads(tmp_path: Path) -> None:
    fs = _fs()
    f = tmp_path / "data.bin"
    f.write_bytes(b"abcdef")
    vp = fs.path(f)
    reader = fs.read(vp)
    try:
        assert reader.read(3) == b"abc"
        assert reader.read(3) == b"def"
    finally:
        reader.close()


# ── write ─────────────────────────────────────────────────────────────────────


def test_write_creates_file(tmp_path: Path) -> None:
    fs = _fs()
    f = tmp_path / "out.bin"
    vp = fs.path(f)
    writer = fs.write(vp)
    try:
        writer.write(b"hello")
    finally:
        writer.close()
    assert f.read_bytes() == b"hello"


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    fs = _fs()
    f = tmp_path / "new" / "sub" / "out.bin"
    vp = fs.path(f)
    writer = fs.write(vp)
    try:
        writer.write(b"data")
    finally:
        writer.close()
    assert f.read_bytes() == b"data"


# ── remove ────────────────────────────────────────────────────────────────────


def test_remove_deletes_file(tmp_path: Path) -> None:
    fs = _fs()
    f = tmp_path / "todelete.txt"
    f.write_text("x")
    fs.remove(fs.path(f))
    assert not f.exists()


# ── rename ────────────────────────────────────────────────────────────────────


def test_rename_moves_file(tmp_path: Path) -> None:
    fs = _fs()
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("content")
    fs.rename(fs.path(src), fs.path(dst))
    assert not src.exists()
    assert dst.read_text() == "content"


# ── rmdir ─────────────────────────────────────────────────────────────────────


def test_rmdir_removes_empty_directory(tmp_path: Path) -> None:
    fs = _fs()
    d = tmp_path / "emptydir"
    d.mkdir()
    fs.rmdir(fs.path(d))
    assert not d.exists()


# ── mkdir ─────────────────────────────────────────────────────────────────────


def test_mkdir_creates_directory(tmp_path: Path) -> None:
    fs = _fs()
    d = tmp_path / "newdir"
    fs.mkdir(fs.path(d))
    assert d.is_dir()


def test_mkdir_raises_if_already_exists(tmp_path: Path) -> None:
    fs = _fs()
    d = tmp_path / "existing"
    d.mkdir()
    with pytest.raises(FileExistsError):
        fs.mkdir(fs.path(d))


# ── copy_stat ─────────────────────────────────────────────────────────────────


def test_copy_stat_sets_mtime_and_mode(tmp_path: Path) -> None:
    fs = _fs()
    f = tmp_path / "file.txt"
    f.write_text("x")
    vp = fs.path(f)
    target_mtime = 1_000_000.0
    target_mode = 0o644
    fs.copy_stat(vp, Stat(modified=target_mtime, mode=target_mode))
    result = os.stat(f)
    assert result.st_mtime == pytest.approx(target_mtime)
    assert stat_mod.S_IMODE(result.st_mode) == target_mode


def test_copy_stat_skips_negative_values(tmp_path: Path) -> None:
    fs = _fs()
    f = tmp_path / "file.txt"
    f.write_text("x")
    vp = fs.path(f)
    original_stat = os.stat(f)
    fs.copy_stat(vp, Stat(modified=-1.0, mode=-1))
    result = os.stat(f)
    assert result.st_mtime == original_stat.st_mtime
    assert result.st_mode == original_stat.st_mode


# ── readlink ──────────────────────────────────────────────────────────────────


def test_readlink_returns_target(tmp_path: Path) -> None:
    fs = _fs()
    target = tmp_path / "target.txt"
    target.write_text("data")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    assert fs.readlink(fs.path(link)) == str(target)
