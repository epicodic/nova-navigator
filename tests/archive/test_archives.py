"""Unit tests for TarArchive and ZipArchive."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path, PurePath

import pytest

from nova_navigator.archive.archive import Archive
from nova_navigator.archive.tar_archive import TarArchive
from nova_navigator.archive.zip_archive import ZipArchive

# ---------------------------------------------------------------------------
# Archive structure used by all fixtures
#
#  dir1/
#  dir1/file11.txt        (17 bytes, mode 0o644)
#  dir1/file12.txt        (14 bytes, mode 0o644)
#  dir11/                 <- sibling with "dir1" prefix — tests prefix safety
#  dir11/other.txt        (5 bytes)
#  dir2/
#  dir2/dir21/
#  dir2/dir21/nested.txt  (14 bytes)
#  dir_empty/
#  executable.sh          (10 bytes, mode 0o755)
#  .hidden_file           (6 bytes)
# ---------------------------------------------------------------------------

_FILE11 = b"hello from file11"  # 17 bytes
_FILE12 = b"hi from file12"  # 14 bytes
_OTHER = b"other"  # 5 bytes
_NESTED = b"nested content"  # 14 bytes
_EXEC = b"#!/bin/sh\n"  # 10 bytes
_HIDDEN = b"secret"  # 6 bytes


def _build_tar(path: Path) -> None:
    with tarfile.open(path, mode="w:gz") as tar:

        def add_dir(name: str) -> None:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)

        def add_file(name: str, content: bytes, mode: int = 0o644) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = mode
            tar.addfile(info, io.BytesIO(content))

        add_dir("dir1")
        add_file("dir1/file11.txt", _FILE11)
        add_file("dir1/file12.txt", _FILE12)
        add_dir("dir11")
        add_file("dir11/other.txt", _OTHER)
        add_dir("dir2")
        add_dir("dir2/dir21")
        add_file("dir2/dir21/nested.txt", _NESTED)
        add_dir("dir_empty")
        add_file("executable.sh", _EXEC, mode=0o755)
        add_file(".hidden_file", _HIDDEN)


def _build_zip(path: Path) -> None:
    with zipfile.ZipFile(path, mode="w") as zf:

        def add_dir(name: str) -> None:
            info = zipfile.ZipInfo(name + "/")
            info.external_attr = 0o755 << 16
            zf.writestr(info, "")

        def add_file(name: str, content: bytes, mode: int = 0o644) -> None:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            zf.writestr(info, content)

        add_dir("dir1")
        add_file("dir1/file11.txt", _FILE11)
        add_file("dir1/file12.txt", _FILE12)
        add_dir("dir11")
        add_file("dir11/other.txt", _OTHER)
        add_dir("dir2")
        add_dir("dir2/dir21")
        add_file("dir2/dir21/nested.txt", _NESTED)
        add_dir("dir_empty")
        add_file("executable.sh", _EXEC, mode=0o755)
        add_file(".hidden_file", _HIDDEN)


@pytest.fixture
def tar_archive(tmp_path: Path) -> TarArchive:
    path = tmp_path / "test.tar.gz"
    _build_tar(path)
    return TarArchive(archive_path=path, mode="r")


@pytest.fixture
def zip_archive(tmp_path: Path) -> ZipArchive:
    path = tmp_path / "test.zip"
    _build_zip(path)
    return ZipArchive(archive_path=path, mode="r")


@pytest.fixture(params=["tar", "zip"])
def archive(request: pytest.FixtureRequest, tmp_path: Path) -> Archive:
    if request.param == "tar":
        path = tmp_path / "test.tar.gz"
        _build_tar(path)
        return TarArchive(archive_path=path, mode="r")
    path = tmp_path / "test.zip"
    _build_zip(path)
    return ZipArchive(archive_path=path, mode="r")


# ---------------------------------------------------------------------------
# listdir()
# ---------------------------------------------------------------------------


def test_listdir_root_returns_all_top_level_entries(archive: Archive) -> None:
    entries = {p.as_posix() for p in archive.listdir(PurePath("/"))}
    assert entries == {"dir1", "dir11", "dir2", "dir_empty", "executable.sh", ".hidden_file"}


def test_listdir_returns_only_direct_children(archive: Archive) -> None:
    entries = {p.as_posix() for p in archive.listdir(PurePath("dir1"))}
    assert entries == {"file11.txt", "file12.txt"}


def test_listdir_does_not_bleed_into_same_prefix_sibling(archive: Archive) -> None:
    """Listing 'dir1' must not include entries from 'dir11'."""
    entries = {p.as_posix() for p in archive.listdir(PurePath("dir1"))}
    assert entries == {"file11.txt", "file12.txt"}


def test_listdir_nested_dir_returns_subdirectory(archive: Archive) -> None:
    entries = {p.as_posix() for p in archive.listdir(PurePath("dir2"))}
    assert entries == {"dir21"}


def test_listdir_deeply_nested_directory(archive: Archive) -> None:
    entries = {p.as_posix() for p in archive.listdir(PurePath("dir2/dir21"))}
    assert entries == {"nested.txt"}


def test_listdir_empty_directory_returns_empty_list(archive: Archive) -> None:
    assert archive.listdir(PurePath("dir_empty")) == []


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------


def test_stats_root_is_directory(archive: Archive) -> None:
    assert archive.stats(PurePath("/")).is_directory


def test_stats_directory_is_directory_with_zero_size(archive: Archive) -> None:
    s = archive.stats(PurePath("dir1"))
    assert s.is_directory
    assert s.size == 0


def test_stats_file_size_is_correct(archive: Archive) -> None:
    assert archive.stats(PurePath("dir1/file11.txt")).size == len(_FILE11)


def test_stats_file_is_not_directory(archive: Archive) -> None:
    assert not archive.stats(PurePath("dir1/file11.txt")).is_directory


def test_stats_hidden_file_is_hidden(archive: Archive) -> None:
    assert archive.stats(PurePath(".hidden_file")).is_hidden


def test_stats_regular_file_is_not_hidden(archive: Archive) -> None:
    assert not archive.stats(PurePath("dir1/file11.txt")).is_hidden


def test_stats_executable_file_is_executable(archive: Archive) -> None:
    assert archive.stats(PurePath("executable.sh")).is_executable


def test_stats_regular_file_is_not_executable(archive: Archive) -> None:
    assert not archive.stats(PurePath("dir1/file11.txt")).is_executable


def test_stats_missing_path_raises_file_not_found(archive: Archive) -> None:
    with pytest.raises(FileNotFoundError):
        archive.stats(PurePath("no/such/file.txt"))


# ---------------------------------------------------------------------------
# TarArchive-specific
# ---------------------------------------------------------------------------


def test_tar_stats_modified_is_numeric(tar_archive: TarArchive) -> None:
    """TarArchive.stats() exposes the numeric mtime stored in the member."""
    s = tar_archive.stats(PurePath("dir1/file11.txt"))
    assert isinstance(s.modified, (int, float))
