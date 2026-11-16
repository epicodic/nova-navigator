"""Reopening an archive member or a cached archive mount serves the container's current content."""

from __future__ import annotations

import io
import os
import tarfile
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest

from nova_navigator.archive import Archive, open_archive
from nova_navigator.local_copies.manager import LocalCopyManager
from nova_navigator.vfs.filesystem import StreamReaderLike
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.local_copy_helpers import ScriptedRunner


@asynccontextmanager
async def _managed(tmp_path: Path) -> AsyncIterator[LocalCopyManager]:
    manager = LocalCopyManager(tmp_path / "root", ScriptedRunner(), poll_interval=0.05, settle_time=0.1)
    try:
        yield manager
    finally:
        await manager.shutdown()


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _tar_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _replace_file(target: Path, data: bytes) -> None:
    """Replace *target* by a new file (new inode), like an out-of-band save."""
    staging = target.with_name(target.name + ".new")
    staging.write_bytes(data)
    os.replace(staging, target)


def _local(path: Path) -> VPath:
    return LocalFilesystem.singleton().path(path)


def _remote_dir(tmp_path: Path) -> Path:
    """The directory the stub server serves as 'remote' storage."""
    directory = tmp_path / "remote"
    directory.mkdir(exist_ok=True)
    return directory


def _count_opens(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record every ``open_archive`` call of the archive filesystem module and return the (live) list."""
    opened: list[Path] = []

    def counting(path: Path, *, mode: Literal["r"] = "r") -> Archive:
        opened.append(path)
        return open_archive(path, mode=mode)

    monkeypatch.setattr("nova_navigator.vfs.filesystems.archive.open_archive", counting)
    return opened


def _count_reads(monkeypatch: pytest.MonkeyPatch, fs: SSHFilesystem) -> list[VPath]:
    """Record every ``read`` of *fs* and return the (live) list."""
    original = fs.read
    reads: list[VPath] = []

    def counting(path: VPath) -> StreamReaderLike:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(fs, "read", counting)
    return reads


def _count_refreshes(monkeypatch: pytest.MonkeyPatch, fs: SSHFilesystem) -> list[VPath | None]:
    """Record every listing-cache ``refresh`` of *fs* and return the (live) list."""
    original = fs.refresh
    refreshed: list[VPath | None] = []

    def counting(path: VPath | None = None) -> None:
        refreshed.append(path)
        original(path)

    monkeypatch.setattr(fs, "refresh", counting)
    return refreshed


def _read(fs: ArchiveFilesystem, member: str) -> bytes:
    return fs.read(fs.path(member)).read(1_000_000)


def _mounted(mount: ArchiveFilesystem | None) -> ArchiveFilesystem:
    assert mount is not None
    return mount


# ---------------------------------------------------------------------------
# Members of a local archive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_zip_member_reopen_after_the_container_was_replaced(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    archive.write_bytes(_zip_bytes({"m.txt": b"old"}))
    async with _managed(tmp_path) as manager:
        mount = _mounted(await manager.mount_archive(_local(archive)))
        entry = await manager.open(mount.path("/m.txt"))
        assert entry is not None
        assert entry.copy.path.read_bytes() == b"old"
        _replace_file(archive, _zip_bytes({"m.txt": b"a much longer new content"}))

        reopened = await manager.open(mount.path("/m.txt"))

        assert reopened is not None
        assert reopened.copy.path.read_bytes() == b"a much longer new content"


@pytest.mark.asyncio
async def test_local_tar_member_reopen_after_the_container_was_replaced(tmp_path: Path) -> None:
    archive = tmp_path / "a.tar"
    archive.write_bytes(_tar_bytes({"m.txt": b"old"}))
    async with _managed(tmp_path) as manager:
        mount = _mounted(await manager.mount_archive(_local(archive)))
        entry = await manager.open(mount.path("/m.txt"))
        assert entry is not None
        _replace_file(archive, _tar_bytes({"m.txt": b"a much longer new content"}))

        reopened = await manager.open(mount.path("/m.txt"))

        assert reopened is not None
        assert reopened.copy.path.read_bytes() == b"a much longer new content"


@pytest.mark.asyncio
async def test_local_zip_member_reopen_unchanged_keeps_the_copy_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "a.zip"
    archive.write_bytes(_zip_bytes({"m.txt": b"same"}))
    async with _managed(tmp_path) as manager:
        mount = _mounted(await manager.mount_archive(_local(archive)))
        entry = await manager.open(mount.path("/m.txt"))
        assert entry is not None
        inode = entry.copy.path.stat().st_ino
        opened = _count_opens(monkeypatch)

        reopened = await manager.open(mount.path("/m.txt"))

        assert reopened is entry
        assert entry.copy.path.stat().st_ino == inode
        assert opened == []


# ---------------------------------------------------------------------------
# Cached mounts and members of a remote archive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cached_remote_archive_mount_is_refreshed_after_the_remote_archive_changed(ssh_fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = _remote_dir(tmp_path) / "a.zip"
    remote.write_bytes(_zip_bytes({"m.txt": b"old"}))
    source = VPath(remote, ssh_fs)
    ssh_fs.stat(source)
    refreshed = _count_refreshes(monkeypatch, ssh_fs)
    async with _managed(tmp_path) as manager:
        mount = _mounted(await manager.mount_archive(source))
        assert _read(mount, "/m.txt") == b"old"
        remote.write_bytes(_zip_bytes({"m.txt": b"a much longer new content"}))

        again = await manager.mount_archive(source)

        assert again is mount
        assert _read(mount, "/m.txt") == b"a much longer new content"
        assert refreshed == []


@pytest.mark.asyncio
async def test_cached_remote_archive_mount_unchanged_neither_downloads_nor_reopens(gnu_fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = _remote_dir(tmp_path) / "a.zip"
    remote.write_bytes(_zip_bytes({"m.txt": b"same"}))
    source = VPath(remote, gnu_fs)
    async with _managed(tmp_path) as manager:
        mount = _mounted(await manager.mount_archive(source))
        reads = _count_reads(monkeypatch, gnu_fs)
        opened = _count_opens(monkeypatch)

        again = await manager.mount_archive(source)

        assert again is mount
        assert reads == []
        assert opened == []
        assert _read(mount, "/m.txt") == b"same"


@pytest.mark.asyncio
async def test_remote_archive_member_reopen_after_the_remote_archive_changed(ssh_fs: SSHFilesystem, tmp_path: Path) -> None:
    remote = _remote_dir(tmp_path) / "a.zip"
    remote.write_bytes(_zip_bytes({"m.txt": b"old"}))
    source = VPath(remote, ssh_fs)
    ssh_fs.stat(source)
    async with _managed(tmp_path) as manager:
        mount = _mounted(await manager.mount_archive(source))
        entry = await manager.open(mount.path("/m.txt"))
        assert entry is not None
        assert entry.copy.path.read_bytes() == b"old"
        remote.write_bytes(_zip_bytes({"m.txt": b"a much longer new content"}))

        reopened = await manager.open(mount.path("/m.txt"))

        assert reopened is not None
        assert reopened.copy.path.read_bytes() == b"a much longer new content"


@pytest.mark.asyncio
async def test_remote_archive_member_reopen_unchanged_does_not_download(ssh_fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = _remote_dir(tmp_path) / "a.zip"
    remote.write_bytes(_zip_bytes({"m.txt": b"same"}))
    source = VPath(remote, ssh_fs)
    ssh_fs.stat(source)
    async with _managed(tmp_path) as manager:
        mount = _mounted(await manager.mount_archive(source))
        entry = await manager.open(mount.path("/m.txt"))
        assert entry is not None
        await manager.release(entry)
        reads = _count_reads(monkeypatch, ssh_fs)

        reopened = await manager.open(mount.path("/m.txt"))

        assert reopened is not None
        assert reopened.copy.path.read_bytes() == b"same"
        assert reads == []


# ---------------------------------------------------------------------------
# Nested archives
# ---------------------------------------------------------------------------


def _nested(content: bytes) -> bytes:
    """An outer zip holding an inner zip with ``m.txt`` set to *content*."""
    return _zip_bytes({"inner.zip": _zip_bytes({"m.txt": content})})


@pytest.mark.asyncio
async def test_nested_zip_member_reopen_after_a_local_outer_archive_changed(tmp_path: Path) -> None:
    outer = tmp_path / "outer.zip"
    outer.write_bytes(_nested(b"old"))
    async with _managed(tmp_path) as manager:
        outer_mount = _mounted(await manager.mount_archive(_local(outer)))
        inner_mount = _mounted(await manager.mount_archive(outer_mount.path("/inner.zip")))
        entry = await manager.open(inner_mount.path("/m.txt"))
        assert entry is not None
        assert entry.copy.path.read_bytes() == b"old"
        _replace_file(outer, _nested(b"a much longer new content"))

        reopened = await manager.open(inner_mount.path("/m.txt"))

        assert reopened is not None
        assert reopened.copy.path.read_bytes() == b"a much longer new content"


@pytest.mark.asyncio
async def test_nested_zip_member_reopen_after_a_remote_outer_archive_changed(gnu_fs: SSHFilesystem, tmp_path: Path) -> None:
    remote = _remote_dir(tmp_path) / "outer.zip"
    remote.write_bytes(_nested(b"old"))
    source = VPath(remote, gnu_fs)
    gnu_fs.stat(source)
    async with _managed(tmp_path) as manager:
        outer_mount = _mounted(await manager.mount_archive(source))
        inner_mount = _mounted(await manager.mount_archive(outer_mount.path("/inner.zip")))
        entry = await manager.open(inner_mount.path("/m.txt"))
        assert entry is not None
        assert entry.copy.path.read_bytes() == b"old"
        remote.write_bytes(_nested(b"a much longer new content"))

        reopened = await manager.open(inner_mount.path("/m.txt"))

        assert reopened is not None
        assert reopened.copy.path.read_bytes() == b"a much longer new content"


@pytest.mark.asyncio
async def test_nested_member_edit_writes_back_to_the_inner_archive(tmp_path: Path) -> None:
    outer = tmp_path / "outer.zip"
    outer.write_bytes(_nested(b"old"))
    async with _managed(tmp_path) as manager:
        outer_mount = _mounted(await manager.mount_archive(_local(outer)))
        inner_mount = _mounted(await manager.mount_archive(outer_mount.path("/inner.zip")))
        entry = await manager.open(inner_mount.path("/m.txt"))
        assert entry is not None
        entry.copy.path.write_bytes(b"edited")

        await manager.release(entry)

        with zipfile.ZipFile(outer) as outer_zip, outer_zip.open("inner.zip") as inner_file:
            inner_bytes = inner_file.read()
        with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner_zip:
            assert inner_zip.read("m.txt") == b"edited"
