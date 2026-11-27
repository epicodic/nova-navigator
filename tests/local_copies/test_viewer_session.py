"""open_for_viewing and finish_viewing: a local file is viewed in place, any other file through a private copy that is never written back."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from nova_navigator.local_copies import LocalCopyManager
from nova_navigator.local_copies.viewer_session import finish_viewing, open_for_viewing
from nova_navigator.vfs.filesystems import ArchiveFilesystem, LocalFilesystem
from tests._utils.local_copy_helpers import SchemeFs, ScriptedRunner


def _manager(tmp_path: Path, runner: ScriptedRunner) -> LocalCopyManager:
    return LocalCopyManager(tmp_path / "copies", runner, poll_interval=0.05, settle_time=0.05)


@pytest.mark.asyncio
async def test_a_local_file_is_viewed_in_place(tmp_path: Path) -> None:
    file = tmp_path / "a.txt"
    file.write_text("hello")
    manager = _manager(tmp_path, ScriptedRunner())
    source = LocalFilesystem.singleton().path(file)

    target = await open_for_viewing(manager, source)

    assert target is not None
    assert target.path == file
    assert target.directory is None
    assert not (tmp_path / "copies").exists()
    finish_viewing(manager, target)
    assert file.read_text() == "hello"


@pytest.mark.asyncio
async def test_a_directory_raises_is_a_directory_error(tmp_path: Path) -> None:
    manager = _manager(tmp_path, ScriptedRunner())

    with pytest.raises(IsADirectoryError):
        await open_for_viewing(manager, LocalFilesystem.singleton().path(tmp_path))


@pytest.mark.asyncio
async def test_a_missing_file_raises_os_error(tmp_path: Path) -> None:
    manager = _manager(tmp_path, ScriptedRunner())

    with pytest.raises(FileNotFoundError):
        await open_for_viewing(manager, LocalFilesystem.singleton().path(tmp_path / "missing.txt"))


@pytest.mark.asyncio
async def test_a_zip_member_is_viewed_through_a_private_copy_that_is_never_written_back(tmp_path: Path) -> None:
    zip_path = tmp_path / "x.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.txt", "member")
    before_bytes = zip_path.read_bytes()
    before_mtime = zip_path.stat().st_mtime_ns
    local = LocalFilesystem.singleton()
    member = ArchiveFilesystem(local.path(tmp_path), local.path(zip_path)).path("/a.txt")
    manager = _manager(tmp_path, ScriptedRunner())

    first = await open_for_viewing(manager, member)
    second = await open_for_viewing(manager, member)

    assert first is not None
    assert second is not None
    views = tmp_path / "copies" / "view"
    assert views in first.path.parents
    assert first.path.read_bytes() == b"member"
    assert first.directory is not None
    assert second.directory is not None
    assert first.directory != second.directory
    assert manager.entries == []
    finish_viewing(manager, first)
    assert not first.directory.exists()
    assert second.directory.exists()
    finish_viewing(manager, second)
    assert zip_path.read_bytes() == before_bytes
    assert zip_path.stat().st_mtime_ns == before_mtime


@pytest.mark.asyncio
async def test_a_failed_download_returns_none_and_leaves_no_copy(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"remote"}, read_errors={"/d/f.txt": OSError("connection reset")})
    manager = _manager(tmp_path, ScriptedRunner())

    target = await open_for_viewing(manager, fs.path("/d/f.txt"))

    assert target is None
    views = tmp_path / "copies" / "view"
    assert not views.exists() or list(views.iterdir()) == []
    assert manager.entries == []
