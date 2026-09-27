"""Tests for LocalCopy path mapping, baselines, and write-back."""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

import pytest

from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.local_copy import relative_copy_path, sanitize_segment
from tests._utils.local_copy_helpers import SchemeFs

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
