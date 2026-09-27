"""Tests for LocalCopy path mapping, baselines, and write-back."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.local_copy import LocalCopy, ReuseAction, relative_copy_path, sanitize_segment
from tests._utils.local_copy_helpers import SchemeFs, overwrite

# ---------------------------------------------------------------------------
# Task 7: path mapping
# ---------------------------------------------------------------------------


def test_remote_path_mirrors_uri() -> None:
    fs = SchemeFs({"/etc/hosts": b"x"})
    assert relative_copy_path(fs.path("/etc/hosts")) == PurePosixPath("ssh/user@host:2222/etc/hosts")


@pytest.mark.parametrize("segment", ["", ".", ".."])
def test_unsafe_segments_are_rejected(segment: str) -> None:
    with pytest.raises(ValueError, match="Unsafe path segment"):
        sanitize_segment(segment)


def test_long_segment_is_shortened_and_keeps_suffix() -> None:
    long = "a" * 300 + ".txt"
    short = sanitize_segment(long)
    assert len(short.encode()) <= 255
    assert short.endswith(".txt")
    assert sanitize_segment(long) == short


def test_archive_member_mirrors_container_and_member_path(tmp_path: Path) -> None:
    zip_path = tmp_path / "x.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("dir/a.txt", "hi")
    local = LocalFilesystem.singleton()
    archive_fs = ArchiveFilesystem(local.path(tmp_path), local.path(zip_path))
    member = archive_fs.path("/dir/a.txt")
    archive_segments = PurePosixPath(*[part for part in tmp_path.parts if part != "/"])
    expected = PurePosixPath("archive/local") / archive_segments / "x.zip" / "dir/a.txt"
    assert relative_copy_path(member) == expected


# ---------------------------------------------------------------------------
# Task 8: LocalCopy — create, baseline, reuse decision, write-back
# ---------------------------------------------------------------------------


def _copy(tmp_path: Path, files: dict[str, bytes | None]) -> tuple[SchemeFs, LocalCopy]:
    fs = SchemeFs(files)
    return fs, LocalCopy.create(fs.path("/d/f.txt"), tmp_path)


def test_create_downloads_and_sets_baseline(tmp_path: Path) -> None:
    _, copy = _copy(tmp_path, {"/d/f.txt": b"hello"})
    assert copy.path == tmp_path / "ssh/user@host:2222/d/f.txt"
    assert copy.path.read_bytes() == b"hello"
    assert copy.path.parent.stat().st_mode & 0o777 == 0o700
    assert copy.path.stat().st_mode & 0o777 == 0o600
    assert not copy.is_modified()
    assert copy.reuse_action() is ReuseAction.REUSE


def test_modified_local_and_unchanged_source_reuses(tmp_path: Path) -> None:
    _, copy = _copy(tmp_path, {"/d/f.txt": b"hello"})
    copy.path.write_bytes(b"edited")
    assert copy.is_modified()
    assert copy.reuse_action() is ReuseAction.REUSE


def test_changed_source_and_unmodified_local_refreshes(tmp_path: Path) -> None:
    fs, copy = _copy(tmp_path, {"/d/f.txt": b"hello"})
    overwrite(fs, "/d/f.txt", b"server side change")
    assert copy.reuse_action() is ReuseAction.REFRESH
    copy.refresh()
    assert copy.path.read_bytes() == b"server side change"
    assert copy.reuse_action() is ReuseAction.REUSE


def test_both_changed_is_conflict(tmp_path: Path) -> None:
    fs, copy = _copy(tmp_path, {"/d/f.txt": b"hello"})
    overwrite(fs, "/d/f.txt", b"server side change")
    copy.path.write_bytes(b"edited")
    assert copy.reuse_action() is ReuseAction.CONFLICT


def test_write_back_updates_source_and_baseline(tmp_path: Path) -> None:
    fs, copy = _copy(tmp_path, {"/d/f.txt": b"hello"})
    copy.path.write_bytes(b"edited")
    assert not copy.source_changed()
    copy.write_back()
    assert fs.read(fs.path("/d/f.txt")).read(100) == b"edited"
    assert not copy.is_modified()
    assert not copy.source_changed()


def test_read_only_copy_refuses_write_back(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"hello"})
    copy = LocalCopy.create(fs.path("/d/f.txt"), tmp_path, read_only=True)
    assert copy.path.stat().st_mode & 0o777 == 0o400
    with pytest.raises(PermissionError):
        copy.write_back()


def test_read_only_copy_refresh_replaces_content_and_stays_read_only(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"hello"})
    copy = LocalCopy.create(fs.path("/d/f.txt"), tmp_path, read_only=True)
    overwrite(fs, "/d/f.txt", b"server side change")
    copy.refresh()
    assert copy.path.read_bytes() == b"server side change"
    assert copy.path.stat().st_mode & 0o777 == 0o400


def test_local_source_is_pass_through(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_bytes(b"x")
    fs = LocalFilesystem.singleton()
    copy = LocalCopy.create(fs.path(real), tmp_path / "root")
    assert copy.path == real
    assert copy.is_pass_through
    assert not (tmp_path / "root").exists()


@pytest.mark.xfail(strict=True, reason="ArchiveFilesystem.version_tag (ZIP CRC) lands in a later task")
def test_zip_member_content_change_detected_via_crc(tmp_path: Path) -> None:
    zip_path = tmp_path / "x.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("a.txt", "one--")
    original_stat = zip_path.stat()

    local = LocalFilesystem.singleton()
    archive_fs = ArchiveFilesystem(local.path(tmp_path), local.path(zip_path))
    copy = LocalCopy.create(archive_fs.path("/a.txt"), tmp_path / "root")

    # Same member name, identical size and (hard-coded 0.0) modified time, different bytes.
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("a.txt", "two--")
    os.utime(zip_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    archive_fs.reload()

    assert copy.source_changed()
