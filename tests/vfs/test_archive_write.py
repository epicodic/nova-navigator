"""Tests for writing archive members through ArchiveFilesystem."""

import zipfile
from pathlib import Path

import pytest

from nova_navigator.archive.backing import LocalArchiveBacking
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem


def _mount(tmp_path: Path, name: str = "a.zip") -> tuple[Path, ArchiveFilesystem]:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("dir/edit.txt", b"old")
        z.writestr("keep.txt", b"keep")
    local = LocalFilesystem.singleton()
    fs = ArchiveFilesystem(local.path(tmp_path), LocalArchiveBacking(local.path(archive)), work_dir=tmp_path / "work")
    return archive, fs


def _write(fs: ArchiveFilesystem, member: str, data: bytes) -> None:
    writer = fs.write(fs.path(member))
    writer.write(data)
    writer.close()


def test_write_replaces_member_in_local_archive(tmp_path: Path) -> None:
    archive, fs = _mount(tmp_path)
    _write(fs, "/dir/edit.txt", b"new")
    with zipfile.ZipFile(archive) as z:
        assert z.read("dir/edit.txt") == b"new"
        assert z.read("keep.txt") == b"keep"
    assert fs.read(fs.path("/dir/edit.txt")).read(10) == b"new"
    assert fs.stat(fs.path("/dir/edit.txt")).size == 3


def test_read_only_format_rejects_write(tmp_path: Path) -> None:
    _, fs = _mount(tmp_path, "a.jar")
    assert fs.capabilities.read_only
    with pytest.raises(PermissionError):
        fs.write(fs.path("/dir/edit.txt"))


def test_zip_member_version_tag_is_crc(tmp_path: Path) -> None:
    _, fs = _mount(tmp_path)
    before = fs.version_tag(fs.path("/dir/edit.txt"))
    _write(fs, "/dir/edit.txt", b"new")
    assert fs.version_tag(fs.path("/dir/edit.txt")) != before


def test_failed_rebuild_leaves_archive_untouched(tmp_path: Path) -> None:
    archive, fs = _mount(tmp_path)
    original = archive.read_bytes()
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("dir/edit.txt", b"duplicate")
    duplicated = archive.read_bytes()
    fs.reload()
    writer = fs.write(fs.path("/dir/edit.txt"))
    writer.write(b"x")
    with pytest.raises(ValueError, match="Ambiguous"):
        writer.close()
    assert archive.read_bytes() == duplicated != original
    assert list((tmp_path / "work").iterdir()) == []
