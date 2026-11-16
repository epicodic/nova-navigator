"""ArchiveFilesystem refreshes from its container before answering, and reopens only when the local file changed."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import threading
import zipfile
from pathlib import Path
from typing import Literal

import pytest

from nova_navigator.archive import Archive, open_archive
from nova_navigator.archive.backing import CopiedArchiveBacking, LocalArchiveBacking
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.local_copy import LocalCopy, SourceFingerprint
from nova_navigator.vfs.vpath import VPath
from tests._utils.local_copy_helpers import SchemeFs, overwrite

_MEMBER = "/dir/edit.txt"


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _count_opens(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record every ``open_archive`` call of the archive filesystem module and return the (live) list."""
    opened: list[Path] = []

    def counting(path: Path, *, mode: Literal["r"] = "r") -> Archive:
        opened.append(path)
        return open_archive(path, mode=mode)

    monkeypatch.setattr("nova_navigator.vfs.filesystems.archive.open_archive", counting)
    return opened


def _local_mount(tmp_path: Path, members: dict[str, bytes] | None = None) -> tuple[Path, ArchiveFilesystem]:
    archive = tmp_path / "a.zip"
    archive.write_bytes(_zip_bytes(members or {"dir/edit.txt": b"old", "keep.txt": b"keep"}))
    local = LocalFilesystem.singleton()
    fs = ArchiveFilesystem(local.path(tmp_path), LocalArchiveBacking(local.path(archive)), work_dir=tmp_path / "work")
    return archive, fs


def _replace_file(target: Path, data: bytes) -> None:
    """Replace *target* by a new file (new inode), like an out-of-band save."""
    staging = target.with_name(target.name + ".new")
    staging.write_bytes(data)
    os.replace(staging, target)


def _remote_mount(fs: SchemeFs, tmp_path: Path) -> tuple[ArchiveFilesystem, LocalCopy]:
    source = fs.path("/archives/a.zip")
    copy = LocalCopy.create(source, tmp_path)
    return ArchiveFilesystem(source.parent, CopiedArchiveBacking(copy), work_dir=tmp_path / "work"), copy


def test_refresh_from_source_reopens_only_when_the_local_file_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fs = SchemeFs({"/archives/a.zip": _zip_bytes({"dir/edit.txt": b"old", "keep.txt": b"keep"})})
    archive_fs, _ = _remote_mount(fs, tmp_path)
    opened = _count_opens(monkeypatch)

    archive_fs.refresh_from_source()
    assert opened == []

    overwrite(fs, "/archives/a.zip", _zip_bytes({"dir/edit.txt": b"old", "keep.txt": b"changed"}))
    archive_fs.refresh_from_source()
    assert len(opened) == 1
    assert archive_fs.read(archive_fs.path("/keep.txt")).read(10) == b"changed"


def test_refresh_from_source_reopens_after_a_local_archive_was_replaced(tmp_path: Path) -> None:
    archive, fs = _local_mount(tmp_path)
    _replace_file(archive, _zip_bytes({"dir/edit.txt": b"old", "keep.txt": b"replaced"}))

    fs.refresh_from_source()

    assert fs.read(fs.path("/keep.txt")).read(20) == b"replaced"


def test_stat_uncached_reflects_a_local_archive_replaced_on_disk(tmp_path: Path) -> None:
    archive, fs = _local_mount(tmp_path)
    assert fs.stat(fs.path(_MEMBER)).size == 3
    _replace_file(archive, _zip_bytes({"dir/edit.txt": b"a much longer content"}))

    assert fs.stat_uncached(fs.path(_MEMBER)).size == len(b"a much longer content")


def test_stat_uncached_on_a_pre_opened_archive_needs_no_backing(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    archive.write_bytes(_zip_bytes({"dir/edit.txt": b"old"}))
    local = LocalFilesystem.singleton()
    fs = ArchiveFilesystem(local.path(tmp_path), open_archive(archive, mode="r"))

    assert fs.stat_uncached(fs.path(_MEMBER)).size == 3


def test_stat_uncached_on_a_closed_mount_raises_value_error(tmp_path: Path) -> None:
    _, fs = _local_mount(tmp_path)
    fs.close()

    with pytest.raises(ValueError, match="closed"):
        fs.stat_uncached(fs.path(_MEMBER))


def test_refresh_from_source_on_a_closed_pre_opened_mount_raises_value_error(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    archive.write_bytes(_zip_bytes({"dir/edit.txt": b"old"}))
    local = LocalFilesystem.singleton()
    fs = ArchiveFilesystem(local.path(tmp_path), open_archive(archive, mode="r"))
    fs.close()

    with pytest.raises(ValueError, match="Archive mount is closed"):
        fs.refresh_from_source()


def test_commit_then_fingerprint_does_not_reopen_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, fs = _local_mount(tmp_path)
    opened = _count_opens(monkeypatch)

    member = fs.path(_MEMBER)
    assert member.stat.size == 3
    writer = fs.write(member)
    writer.write(b"newer content")
    writer.close()
    assert len(opened) == 1

    fingerprint = SourceFingerprint.of(member)

    assert len(opened) == 1
    assert fingerprint.size == len(b"newer content")
    assert member.stat.size == len(b"newer content")


def test_write_back_of_a_member_does_not_deadlock_on_the_mount_lock(tmp_path: Path) -> None:
    fs = SchemeFs({"/archives/a.zip": _zip_bytes({"dir/edit.txt": b"old", "keep.txt": b"keep"})})
    archive_fs, _ = _remote_mount(fs, tmp_path)
    member_copy = LocalCopy.create(archive_fs.path(_MEMBER), tmp_path / "members")
    member_copy.path.write_bytes(b"edited")
    errors: list[BaseException] = []

    def run() -> None:
        try:
            member_copy.write_back()
        except OSError as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    assert archive_fs.read(archive_fs.path(_MEMBER)).read(10) == b"edited"


def test_reopen_survives_concurrent_stat_calls_adding_members(tmp_path: Path) -> None:
    _, fs = _local_mount(tmp_path)
    live: list[VPath] = []
    for index in range(20_000):
        path = VPath(f"/pre/{index}", fs)
        live.append(path)
        with pytest.raises(FileNotFoundError):
            fs.stat(path)
    stop = threading.Event()
    adder_live: list[VPath] = []

    def keep_adding() -> None:
        index = 0
        while not stop.is_set():
            path = VPath(f"/added/{index}", fs)
            adder_live.append(path)
            index += 1
            with contextlib.suppress(FileNotFoundError):
                fs.stat(path)

    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    adder = threading.Thread(target=keep_adding, daemon=True)
    adder.start()
    try:
        for _ in range(50):
            fs._reopen()
    finally:
        stop.set()
        adder.join(timeout=10)
        sys.setswitchinterval(previous_interval)

    assert not adder.is_alive()
