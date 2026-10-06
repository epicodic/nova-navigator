"""open_for_editing and finish_editing: a local file is edited in place, any other file through a local copy (REQ-21)."""

from __future__ import annotations

import errno
import os
import zipfile
from pathlib import Path

import pytest

from nova_navigator.local_copies import CopyEntry, CopyStatus, LocalCopyManager
from nova_navigator.local_copies.editor_session import check_readable, finish_editing, open_for_editing
from nova_navigator.scheduler import Job
from nova_navigator.vfs.filesystems import ArchiveFilesystem, LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from nova_widgets import Response
from tests._utils.local_copy_helpers import SchemeFs, ScriptedRunner, overwrite


class _RecordingManager(LocalCopyManager):
    """A manager that records open() and must never start a job."""

    def __init__(self, root: Path) -> None:
        super().__init__(root, self._no_jobs)
        self.opened: list[VPath] = []

    async def _no_jobs(self, job: Job) -> None:
        raise AssertionError("no job expected")

    async def open(self, source: VPath) -> CopyEntry | None:
        self.opened.append(source)
        return None


def _manager(tmp_path: Path, runner: ScriptedRunner) -> LocalCopyManager:
    return LocalCopyManager(tmp_path / "copies", runner, poll_interval=0.05, settle_time=0.05)


@pytest.mark.asyncio
async def test_a_local_file_is_edited_in_place_without_a_copy(tmp_path: Path) -> None:
    file = tmp_path / "a.txt"
    file.write_text("hello")
    manager = _RecordingManager(tmp_path / "copies")
    source = VPath(file, LocalFilesystem.singleton())

    target = await open_for_editing(manager, source)

    assert target is not None
    assert target.path == file
    assert target.entry is None
    assert not target.read_only
    assert manager.opened == []
    assert not (tmp_path / "copies").exists()


@pytest.mark.asyncio
async def test_a_remote_file_is_edited_through_its_local_copy(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"remote text"})
    manager = _manager(tmp_path, ScriptedRunner())

    target = await open_for_editing(manager, fs.path("/d/f.txt"))

    assert target is not None
    assert target.entry is not None
    assert target.path == target.entry.copy.path
    assert tmp_path / "copies" in target.path.parents
    assert target.path.read_bytes() == b"remote text"
    assert manager.entries == [target.entry]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_a_member_of_a_local_archive_is_edited_through_a_copy(tmp_path: Path) -> None:
    zip_path = tmp_path / "x.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.txt", "member")
    local = LocalFilesystem.singleton()
    member = ArchiveFilesystem(local.path(tmp_path), local.path(zip_path)).path("/a.txt")
    manager = _manager(tmp_path, ScriptedRunner())

    target = await open_for_editing(manager, member)

    assert target is not None
    assert target.entry is not None
    assert target.path.read_bytes() == b"member"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_a_read_only_copy_is_marked_read_only(tmp_path: Path) -> None:
    jar = tmp_path / "x.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("META-INF/a.txt", "member")
    local = LocalFilesystem.singleton()
    member = ArchiveFilesystem(local.path(tmp_path), local.path(jar)).path("/META-INF/a.txt")
    manager = _manager(tmp_path, ScriptedRunner())

    target = await open_for_editing(manager, member)

    assert target is not None
    assert target.read_only
    assert target.entry is not None
    assert target.entry.status is CopyStatus.READ_ONLY
    await manager.shutdown()


@pytest.mark.asyncio
async def test_a_directory_raises_is_a_directory_error_for_local_and_remote(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"x"})
    manager = _RecordingManager(tmp_path / "copies")
    with pytest.raises(IsADirectoryError) as local_error:
        await open_for_editing(manager, VPath(tmp_path, LocalFilesystem.singleton()))
    assert local_error.value.errno == errno.EISDIR
    assert str(local_error.value) == f"[Errno 21] Is a directory: '{tmp_path}'"
    with pytest.raises(IsADirectoryError):
        await open_for_editing(manager, fs.path("/d"))
    assert manager.opened == []


@pytest.mark.asyncio
async def test_a_missing_local_file_raises_file_not_found(tmp_path: Path) -> None:
    source = VPath(tmp_path / "gone.txt", LocalFilesystem.singleton())
    with pytest.raises(FileNotFoundError):
        await open_for_editing(_RecordingManager(tmp_path / "copies"), source)


def test_check_readable_refuses_a_fifo(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(OSError, match="Not a regular file"):
        check_readable(fifo)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_check_readable_raises_permission_error(tmp_path: Path) -> None:
    file = tmp_path / "secret.txt"
    file.write_text("x")
    file.chmod(0)
    with pytest.raises(PermissionError):
        check_readable(file)


@pytest.mark.asyncio
async def test_a_reopen_conflict_answered_with_cancel_gives_none(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    # a long settle time keeps the copy's own watcher out of the way: only the reopen prompt answers CANCEL
    manager = LocalCopyManager(tmp_path / "copies", ScriptedRunner([Response.CANCEL]), poll_interval=60, settle_time=60)
    first = await open_for_editing(manager, fs.path("/d/f.txt"))
    assert first is not None
    first.path.write_bytes(b"local")
    overwrite(fs, "/d/f.txt", b"server")

    assert await open_for_editing(manager, fs.path("/d/f.txt")) is None

    assert first.path.read_bytes() == b"local"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_finish_editing_of_a_local_file_does_nothing(tmp_path: Path) -> None:
    file = tmp_path / "a.txt"
    file.write_text("hello")
    manager = _RecordingManager(tmp_path / "copies")
    target = await open_for_editing(manager, VPath(file, LocalFilesystem.singleton()))
    assert target is not None
    assert await finish_editing(manager, target) is None


@pytest.mark.asyncio
async def test_finish_editing_writes_the_copy_back_and_says_nothing(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    runner = ScriptedRunner()
    manager = _manager(tmp_path, runner)
    target = await open_for_editing(manager, fs.path("/d/f.txt"))
    assert target is not None
    target.path.write_bytes(b"new")

    assert await finish_editing(manager, target) is None

    assert fs.read(fs.path("/d/f.txt")).read(10) == b"new"
    assert len(runner.sync_jobs) == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_finish_editing_reports_a_failed_write_back(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"}, write_errors={"/d/f.txt": OSError("disk full")})
    manager = _manager(tmp_path, ScriptedRunner())
    target = await open_for_editing(manager, fs.path("/d/f.txt"))
    assert target is not None
    target.path.write_bytes(b"new")

    notice = await finish_editing(manager, target)

    assert notice is not None
    assert notice.severity == "error"
    assert "Could not write f.txt back" in notice.message
    assert "disk full" in notice.message
    assert target.path.read_bytes() == b"new"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_finish_editing_reports_a_conflict_and_leaves_the_source(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    manager = _manager(tmp_path, ScriptedRunner([Response.SKIP]))
    target = await open_for_editing(manager, fs.path("/d/f.txt"))
    assert target is not None
    overwrite(fs, "/d/f.txt", b"server")
    target.path.write_bytes(b"local")

    notice = await finish_editing(manager, target)

    assert notice is not None
    assert notice.severity == "warning"
    assert "f.txt changed on the source" in notice.message
    assert fs.read(fs.path("/d/f.txt")).read(10) == b"server"
    await manager.shutdown()
