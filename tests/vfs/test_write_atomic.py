"""Tests for atomic replacement and version tags."""

import os
from pathlib import Path

import pytest

from nova_navigator.vfs.filesystems import local as local_module
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from tests._utils.mock_filesystem import MockFilesystem


def test_local_write_atomic_replaces_and_keeps_mode(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    target.chmod(0o640)
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"new")
    assert target.read_bytes() == b"old"
    writer.close()
    assert target.read_bytes() == b"new"
    assert target.stat().st_mode & 0o777 == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_local_write_atomic_abort_keeps_original(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"partial")
    writer.abort()
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_local_write_atomic_new_file_uses_umask_default(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"data")
    writer.close()
    assert target.read_bytes() == b"data"
    assert target.stat().st_mode & 0o777 == 0o666 & ~local_module._PROCESS_UMASK


def test_local_write_atomic_replace_failure_leaves_target_and_cleans_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"new")

    def _raise_replace(_src: object, _dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(local_module.os, "replace", _raise_replace)
    with pytest.raises(OSError, match="replace failed"):
        writer.close()
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_local_write_atomic_close_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"new")
    writer.close()
    writer.close()
    assert target.read_bytes() == b"new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_local_write_atomic_close_after_abort_is_noop(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"partial")
    writer.abort()
    writer.close()
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_local_write_atomic_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_bytes(b"x")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    fs = LocalFilesystem.singleton()
    with pytest.raises(ValueError, match="symlink"):
        fs.write_atomic(fs.path(link))


def test_local_version_tag_changes_on_replace(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"same")
    fs = LocalFilesystem.singleton()
    before = fs.version_tag(fs.path(target))
    replacement = tmp_path / "g.txt"
    replacement.write_bytes(b"same")
    os.utime(replacement, ns=(target.stat().st_atime_ns, target.stat().st_mtime_ns))
    os.replace(replacement, target)
    assert fs.version_tag(fs.path(target)) != before


def test_default_write_atomic_uploads_only_on_close() -> None:
    fs = MockFilesystem({"/a.txt": b"old"})
    writer = fs.write_atomic(fs.path("/a.txt"))
    writer.write(b"new")
    assert fs.read(fs.path("/a.txt")).read(10) == b"old"
    writer.close()
    assert fs.read(fs.path("/a.txt")).read(10) == b"new"


def test_default_write_atomic_abort_never_writes() -> None:
    fs = MockFilesystem({"/a.txt": b"old"})
    writer = fs.write_atomic(fs.path("/a.txt"))
    writer.write(b"new")
    writer.abort()
    assert fs.read(fs.path("/a.txt")).read(10) == b"old"
    assert fs.writers == []
