"""Unit tests for TarArchive and ZipArchive."""

from __future__ import annotations

import io
import tarfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePath
from typing import IO, Any, cast

import pytest

from nova_navigator.archive.archive import Archive
from nova_navigator.archive.tar_archive import TarArchive
from nova_navigator.archive.zip_archive import ZipArchive
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath

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


@pytest.mark.parametrize(("path", "expected"), [("dir1/file11.txt", _FILE11), ("dir2/dir21/nested.txt", _NESTED)])
def test_read_returns_file_bytes(archive: Archive, path: str, expected: bytes) -> None:
    reader = archive.read(PurePath(path))
    try:
        assert reader.read(4) + reader.read(100) == expected
    finally:
        reader.close()


def test_read_missing_file_raises_file_not_found(archive: Archive) -> None:
    with pytest.raises(FileNotFoundError):
        archive.read(PurePath("no/such/file.txt"))


@pytest.mark.parametrize("path", ["dir1", "/"])
def test_read_directory_raises_is_a_directory(archive: Archive, path: str) -> None:
    with pytest.raises(IsADirectoryError):
        archive.read(PurePath(path))


def test_archive_filesystem_read_delegates_to_archive(archive: Archive, tmp_path: Path) -> None:
    local = LocalFilesystem.singleton()
    filesystem = ArchiveFilesystem(VPath(tmp_path, local), archive)
    reader = filesystem.read(filesystem.path("/dir1/file11.txt"))
    try:
        assert reader.read(100) == _FILE11
    finally:
        reader.close()


def test_archive_filesystem_read_rejects_foreign_path(archive: Archive, tmp_path: Path) -> None:
    local = LocalFilesystem.singleton()
    filesystem = ArchiveFilesystem(VPath(tmp_path, local), archive)
    with pytest.raises(ValueError, match="does not belong to filesystem"):
        filesystem.read(VPath("/dir1/file11.txt", local))


@pytest.mark.parametrize("archive_type", ["tar", "zip"])
@pytest.mark.parametrize("directory", ["implicit", "implicit/nested"])
def test_read_implicit_directory_raises_is_a_directory(tmp_path: Path, archive_type: str, directory: str) -> None:
    member_name = "implicit/nested/file.txt"
    if archive_type == "tar":
        archive_path = tmp_path / "implicit.tar.gz"
        with tarfile.open(archive_path, mode="w:gz") as tar:
            info = tarfile.TarInfo(member_name)
            info.size = len(_OTHER)
            tar.addfile(info, io.BytesIO(_OTHER))
        archive = TarArchive(archive_path, mode="r")
    else:
        archive_path = tmp_path / "implicit.zip"
        with zipfile.ZipFile(archive_path, mode="w") as zf:
            zf.writestr(member_name, _OTHER)
        archive = ZipArchive(archive_path, mode="r")

    with pytest.raises(IsADirectoryError):
        archive.read(PurePath(directory))


def test_tar_concurrent_readers_return_their_own_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive_path = tmp_path / "concurrent.tar"
    contents = {"a.bin": b"a" * 4096, "b.bin": b"b" * 4096}
    with tarfile.open(archive_path, mode="w") as tar:
        for name, data in contents.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    archive = TarArchive(archive_path, mode="r")
    fileobj = archive._tar_file.fileobj
    assert fileobj is not None

    class InterleavingFile:
        def __init__(self, wrapped: IO[bytes]) -> None:
            self._wrapped = wrapped
            self._seek_count = 0
            self._seek_lock = threading.Lock()
            self._second_seek = threading.Event()

        def seek(self, offset: int, whence: int = 0) -> int:
            result = self._wrapped.seek(offset, whence)
            with self._seek_lock:
                self._seek_count += 1
                first = self._seek_count == 1
                if self._seek_count == 2:
                    self._second_seek.set()
            if first:
                self._second_seek.wait(timeout=0.2)
            return result

        def read(self, size: int = -1) -> bytes:
            return self._wrapped.read(size)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._wrapped, name)

    monkeypatch.setattr(archive._tar_file, "fileobj", InterleavingFile(cast("IO[bytes]", fileobj)))
    start = threading.Barrier(2)

    def read_member(name: str) -> bytes:
        reader = archive.read(PurePath(name))
        try:
            start.wait(timeout=2)
            return reader.read(4096)
        finally:
            reader.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {name: executor.submit(read_member, name) for name in contents}
        results = {name: future.result() for name, future in futures.items()}
    assert results == contents


def test_tar_read_dangling_symlink_raises_file_not_found(tmp_path: Path) -> None:
    archive_path = tmp_path / "dangling.tar"
    with tarfile.open(archive_path, mode="w") as tar:
        info = tarfile.TarInfo("dangling.txt")
        info.type = tarfile.SYMTYPE
        info.linkname = "missing.txt"
        tar.addfile(info)

    archive = TarArchive(archive_path, mode="r")
    with pytest.raises(FileNotFoundError):
        archive.read(PurePath("dangling.txt"))


@pytest.fixture(params=["tar", "zip"])
def implicit_archive(request: pytest.FixtureRequest, tmp_path: Path) -> Archive:
    member_name = "implicit/nested/file.txt"
    if request.param == "tar":
        archive_path = tmp_path / "implicit.tar.gz"
        with tarfile.open(archive_path, mode="w:gz") as tar:
            info = tarfile.TarInfo(member_name)
            info.size = len(_OTHER)
            tar.addfile(info, io.BytesIO(_OTHER))
        return TarArchive(archive_path, mode="r")
    archive_path = tmp_path / "implicit.zip"
    with zipfile.ZipFile(archive_path, mode="w") as zf:
        zf.writestr(member_name, _OTHER)
    return ZipArchive(archive_path, mode="r")


@pytest.mark.parametrize("directory", ["implicit", "implicit/nested"])
def test_stats_implicit_directory(implicit_archive: Archive, directory: str) -> None:
    stat = implicit_archive.stats(PurePath(directory))
    assert stat.is_directory
    assert stat.size == 0


@pytest.mark.asyncio
async def test_archive_filesystem_lists_implicit_directory(implicit_archive: Archive, tmp_path: Path) -> None:
    local = LocalFilesystem.singleton()
    filesystem = ArchiveFilesystem(VPath(tmp_path, local), implicit_archive)
    entries = [entry async for entry in filesystem.iterdir(filesystem.root())]
    assert len(entries) == 1
    assert entries[0].name == "implicit"
    assert entries[0].stat.is_directory
    nested = [entry async for entry in filesystem.iterdir(entries[0])]
    assert len(nested) == 1
    assert nested[0].name == "nested"
    assert nested[0].stat.is_directory


def test_close_releases_archive_handle(archive: Archive) -> None:
    archive.close()
    archive.close()
    with pytest.raises((OSError, ValueError)):
        archive.read(PurePath("dir1/file11.txt"))


# ---------------------------------------------------------------------------
# TarArchive — './'-prefixed member names (e.g. `tar -C dir -czf x.tar.gz .`)
# ---------------------------------------------------------------------------

_DOTSLASH_CONTENT = b"hello"


def _build_dotslash_tar(path: Path) -> None:
    with tarfile.open(path, mode="w:gz") as tar:
        root = tarfile.TarInfo("./")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        tar.addfile(root)

        directory = tarfile.TarInfo("./dir/")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        tar.addfile(directory)

        file_info = tarfile.TarInfo("./dir/a.txt")
        file_info.size = len(_DOTSLASH_CONTENT)
        tar.addfile(file_info, io.BytesIO(_DOTSLASH_CONTENT))


@pytest.fixture
def dotslash_tar_archive(tmp_path: Path) -> TarArchive:
    path = tmp_path / "dotslash.tar.gz"
    _build_dotslash_tar(path)
    return TarArchive(archive_path=path, mode="r")


def test_dotslash_prefixed_listdir_root_shows_dir_not_dot(dotslash_tar_archive: TarArchive) -> None:
    entries = {p.as_posix() for p in dotslash_tar_archive.listdir(PurePath("/"))}
    assert entries == {"dir"}


def test_dotslash_prefixed_listdir_nested(dotslash_tar_archive: TarArchive) -> None:
    entries = {p.as_posix() for p in dotslash_tar_archive.listdir(PurePath("dir"))}
    assert entries == {"a.txt"}


def test_dotslash_prefixed_stats_directory(dotslash_tar_archive: TarArchive) -> None:
    assert dotslash_tar_archive.stats(PurePath("dir")).is_directory


def test_dotslash_prefixed_stats_file(dotslash_tar_archive: TarArchive) -> None:
    s = dotslash_tar_archive.stats(PurePath("dir/a.txt"))
    assert not s.is_directory
    assert s.size == len(_DOTSLASH_CONTENT)


def test_dotslash_prefixed_read_returns_file_bytes(dotslash_tar_archive: TarArchive) -> None:
    reader = dotslash_tar_archive.read(PurePath("dir/a.txt"))
    try:
        assert reader.read(100) == _DOTSLASH_CONTENT
    finally:
        reader.close()


def test_write_through_archive_filesystem_targets_dotslash_prefixed_member(tmp_path: Path) -> None:
    """A member write must land on the archive's actual './dir/a.txt' entry, not a new one."""
    archive_path = tmp_path / "dotslash.tar.gz"
    _build_dotslash_tar(archive_path)
    local = LocalFilesystem.singleton()
    fs = ArchiveFilesystem(VPath(tmp_path, local), VPath(archive_path, local), work_dir=tmp_path / "work")

    writer = fs.write(fs.path("/dir/a.txt"))
    writer.write(b"updated")
    writer.close()

    assert fs.read(fs.path("/dir/a.txt")).read(20) == b"updated"
    with tarfile.open(archive_path, "r:gz") as tar:
        names = [info.name for info in tar.getmembers()]
        assert names.count("./dir/a.txt") == 1
        assert "dir/a.txt" not in names
        member = tar.extractfile("./dir/a.txt")
        assert member is not None
        assert member.read() == b"updated"
