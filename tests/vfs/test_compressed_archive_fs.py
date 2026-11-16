"""Compressed single files (.gz, .bz2, .xz) entered through ArchiveFilesystem, local and via a remote copy."""

import bz2
import gzip
import io
import lzma
from collections.abc import Callable
from pathlib import Path

import pytest

from nova_navigator.archive.backing import CopiedArchiveBacking
from nova_navigator.local_copies.manager import LocalCopyManager
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.local_copy import LocalCopy
from tests._utils.local_copy_helpers import SchemeFs, ScriptedRunner, overwrite

_COMPRESSORS: dict[str, Callable[[bytes], bytes]] = {
    ".gz": gzip.compress,
    ".bz2": bz2.compress,
    ".xz": lzma.compress,
}
_SUFFIXES = list(_COMPRESSORS)


def _local_mount(tmp_path: Path, suffix: str, content: bytes = b"hello world") -> tuple[Path, ArchiveFilesystem]:
    path = tmp_path / f"notes.txt{suffix}"
    path.write_bytes(_COMPRESSORS[suffix](content))
    local = LocalFilesystem.singleton()
    return path, ArchiveFilesystem(local.path(tmp_path), local.path(path))


def _read_all(fs: ArchiveFilesystem, member: str) -> bytes:
    reader = fs.read(fs.path(member))
    try:
        return reader.read(1_000_000)
    finally:
        reader.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", _SUFFIXES)
async def test_local_mount_lists_one_entry_and_reads_it(tmp_path: Path, suffix: str) -> None:
    _, fs = _local_mount(tmp_path, suffix)
    entries = [entry async for entry in fs.iterdir(fs.root())]
    assert [entry.name for entry in entries] == ["notes.txt"]
    assert entries[0].stat.size == len(b"hello world")
    assert _read_all(fs, "/notes.txt") == b"hello world"


@pytest.mark.parametrize("suffix", _SUFFIXES)
def test_local_mount_is_read_only(tmp_path: Path, suffix: str) -> None:
    path, fs = _local_mount(tmp_path, suffix)
    before = path.read_bytes()
    assert fs.capabilities.read_only
    with pytest.raises(PermissionError):
        fs.write(fs.path("/notes.txt"))
    with pytest.raises(PermissionError):
        fs.mkdir(fs.path("/sub"))
    with pytest.raises(io.UnsupportedOperation):
        fs.remove(fs.path("/notes.txt"))
    with pytest.raises(io.UnsupportedOperation):
        fs.rename(fs.path("/notes.txt"), fs.path("/other.txt"))
    with pytest.raises(io.UnsupportedOperation):
        fs.rmdir(fs.path("/notes.txt"))
    assert path.read_bytes() == before
    assert _read_all(fs, "/notes.txt") == b"hello world"


@pytest.mark.parametrize("suffix", _SUFFIXES)
@pytest.mark.parametrize("damage", ["empty", "garbage"])
def test_local_mount_of_corrupt_or_empty_file_raises_os_error(tmp_path: Path, suffix: str, damage: str) -> None:
    path = tmp_path / f"bad{suffix}"
    path.write_bytes(b"" if damage == "empty" else b"definitely not compressed" * 10)
    local = LocalFilesystem.singleton()
    with pytest.raises(OSError, match="Corrupt or empty"):
        ArchiveFilesystem(local.path(tmp_path), local.path(path))


def _remote_mount(fs: SchemeFs, remote_path: str, root: Path) -> ArchiveFilesystem:
    vpath = fs.path(remote_path)
    copy = LocalCopy.create(vpath, root, read_only=True)
    return ArchiveFilesystem(vpath.parent, CopiedArchiveBacking(copy), work_dir=root / "work")


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", _SUFFIXES)
async def test_remote_mount_reads_decompressed_content_and_follows_the_source(tmp_path: Path, suffix: str) -> None:
    compress = _COMPRESSORS[suffix]
    fs = SchemeFs({f"/d/notes.txt{suffix}": compress(b"first")})
    archive_fs = _remote_mount(fs, f"/d/notes.txt{suffix}", tmp_path)

    assert archive_fs.capabilities.read_only
    assert [entry.name async for entry in archive_fs.iterdir(archive_fs.root())] == ["notes.txt"]
    assert _read_all(archive_fs, "/notes.txt") == b"first"

    overwrite(fs, f"/d/notes.txt{suffix}", compress(b"second version"))
    archive_fs.refresh_from_source()

    assert _read_all(archive_fs, "/notes.txt") == b"second version"
    assert archive_fs.path("/notes.txt").stat.size == len(b"second version")


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", _SUFFIXES)
async def test_mount_archive_of_corrupt_remote_file_raises_and_registers_nothing(tmp_path: Path, suffix: str) -> None:
    fs = SchemeFs({f"/d/bad{suffix}": b"definitely not compressed" * 10})
    manager = LocalCopyManager(tmp_path / "root", ScriptedRunner(), poll_interval=0.05, settle_time=0.1)

    with pytest.raises(OSError, match="Corrupt or empty"):
        await manager.mount_archive(fs.path(f"/d/bad{suffix}"))

    leftovers = [item for item in (tmp_path / "root").rglob("*") if item.is_file()]
    assert leftovers == []
    await manager.shutdown()
