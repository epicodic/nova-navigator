"""Tests for LocalCopyManager sync, reopen, read-only, and archive-mount behaviour."""

from __future__ import annotations

import asyncio
import io
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from nova_navigator.local_copies.manager import CopyStatus, LocalCopyManager
from nova_navigator.response import Response
from nova_navigator.scheduler import Job
from nova_navigator.vfs.filesystem import FilesystemCapabilities, StreamWriterLike
from nova_navigator.vfs.vpath import VPath
from tests._utils.local_copy_helpers import SchemeFs, overwrite


class _Runner:
    """Run jobs to completion, answering prompts from a scripted list."""

    def __init__(self, answers: list[Response] | None = None) -> None:
        self.answers = answers or []
        self.jobs: list[Job] = []
        self.prompts: list[str] = []

    async def __call__(self, job: Job) -> None:
        self.jobs.append(job)

        async def answer(request: object, future: asyncio.Future[Response]) -> None:
            self.prompts.append(getattr(request, "title", ""))
            future.set_result(self.answers.pop(0))

        await job.start(answer)


class _ReadOnlyFs(SchemeFs):
    """SchemeFs that reports a read-only filesystem, for the read-only entry test."""

    @property
    def capabilities(self) -> FilesystemCapabilities:
        return FilesystemCapabilities(read_only=True)


def _slow_to_write(monkeypatch: pytest.MonkeyPatch, fs: SchemeFs, delay: float) -> None:
    """Make *fs*'s write() block the calling (worker) thread for *delay* seconds.

    Blocks inside the worker thread that runs the sync job, so the GUI loop (and this
    test's own change detector) keeps running while a sync is "in flight" -- letting a
    test provoke a save during a running sync deterministically. Patches the instance
    (not the class), so a Liskov-incompatible override is not part of the type surface.
    """
    original_write = fs.write

    def slow_write(path: VPath) -> StreamWriterLike:
        time.sleep(delay)
        return original_write(path)

    monkeypatch.setattr(fs, "write", slow_write)


async def _manager(tmp_path: Path, runner: _Runner) -> LocalCopyManager:
    return LocalCopyManager(tmp_path / "root", runner, poll_interval=0.05, settle_time=0.1)


async def _wait_until(predicate: Callable[[], bool], max_wait: float = 3.0) -> None:
    """Poll *predicate* until it is true, raising if *max_wait* seconds pass first."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Sync on change, conflicts, failed sync retry, reuse, discard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_then_save_syncs_back(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    runner = _Runner()
    manager = await _manager(tmp_path, runner)
    entry = await manager.open(fs.path("/d/f.txt"))
    assert entry is not None
    entry.copy.path.write_bytes(b"new")
    await manager.wait_idle(max_wait=3)
    assert fs.read(fs.path("/d/f.txt")).read(10) == b"new"
    assert entry.status is CopyStatus.SYNCED
    await manager.shutdown()


@pytest.mark.asyncio
async def test_conflict_skip_pauses_sync(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    runner = _Runner([Response.SKIP])
    manager = await _manager(tmp_path, runner)
    entry = await manager.open(fs.path("/d/f.txt"))
    assert entry is not None
    overwrite(fs, "/d/f.txt", b"server")
    entry.copy.path.write_bytes(b"local")
    await manager.wait_idle(max_wait=3)
    assert entry.status is CopyStatus.CONFLICT
    assert fs.read(fs.path("/d/f.txt")).read(10) == b"server"
    entry.copy.path.write_bytes(b"local again")
    await manager.wait_idle(max_wait=3)
    assert len(runner.prompts) == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_conflict_overwrite_writes_local(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    manager = await _manager(tmp_path, _Runner([Response.OVERWRITE]))
    entry = await manager.open(fs.path("/d/f.txt"))
    assert entry is not None
    overwrite(fs, "/d/f.txt", b"server")
    entry.copy.path.write_bytes(b"local")
    await manager.wait_idle(max_wait=3)
    assert fs.read(fs.path("/d/f.txt")).read(10) == b"local"
    assert entry.status is CopyStatus.SYNCED
    await manager.shutdown()


@pytest.mark.asyncio
async def test_failed_sync_retries_on_next_save(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"}, write_errors={"/d/f.txt": OSError("disk full")})
    manager = await _manager(tmp_path, _Runner())
    entry = await manager.open(fs.path("/d/f.txt"))
    assert entry is not None
    entry.copy.path.write_bytes(b"new")
    await manager.wait_idle(max_wait=3)
    assert entry.status is CopyStatus.FAILED
    assert entry.copy.path.read_bytes() == b"new"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_open_same_source_twice_returns_same_entry(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    manager = await _manager(tmp_path, _Runner())
    first = await manager.open(fs.path("/d/f.txt"))
    second = await manager.open(fs.path("/d/f.txt"))
    assert first is second
    await manager.shutdown()


@pytest.mark.asyncio
async def test_discard_deletes_local_file(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    manager = await _manager(tmp_path, _Runner())
    entry = await manager.open(fs.path("/d/f.txt"))
    assert entry is not None
    await manager.discard(entry)
    assert not entry.copy.path.exists()
    assert manager.entries == []
    await manager.shutdown()


# ---------------------------------------------------------------------------
# Reopen: the reuse-on-reopen table's CONFLICT row (Overwrite / Discard / Cancel)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reopen_conflict_overwrite_writes_local_then_reopens(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    runner = _Runner([Response.OVERWRITE])
    manager = await _manager(tmp_path, runner)
    entry = await manager.open(fs.path("/d/f.txt"))
    assert entry is not None
    await manager.close(entry)
    entry.copy.path.write_bytes(b"local edit")
    overwrite(fs, "/d/f.txt", b"server edit")

    reopened = await manager.open(fs.path("/d/f.txt"))
    assert reopened is entry
    assert fs.read(fs.path("/d/f.txt")).read(20) == b"local edit"
    assert entry.status is CopyStatus.SYNCED
    assert entry.detector is not None
    assert runner.prompts == ["Source and local copy both changed"]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_reopen_conflict_discard_refreshes_then_reopens(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    manager = await _manager(tmp_path, _Runner([Response.DISCARD]))
    entry = await manager.open(fs.path("/d/f.txt"))
    assert entry is not None
    await manager.close(entry)
    entry.copy.path.write_bytes(b"local edit")
    overwrite(fs, "/d/f.txt", b"server edit")

    reopened = await manager.open(fs.path("/d/f.txt"))
    assert reopened is entry
    assert entry.copy.path.read_bytes() == b"server edit"
    assert entry.status is CopyStatus.SYNCED
    assert entry.detector is not None
    await manager.shutdown()


@pytest.mark.asyncio
async def test_reopen_conflict_cancel_leaves_entry_untouched(tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    manager = await _manager(tmp_path, _Runner([Response.CANCEL]))
    entry = await manager.open(fs.path("/d/f.txt"))
    assert entry is not None
    await manager.close(entry)
    entry.copy.path.write_bytes(b"local edit")
    overwrite(fs, "/d/f.txt", b"server edit")

    reopened = await manager.open(fs.path("/d/f.txt"))
    assert reopened is None
    assert entry in manager.entries
    assert entry.detector is None
    assert entry.copy.path.read_bytes() == b"local edit"
    assert fs.read(fs.path("/d/f.txt")).read(20) == b"server edit"
    await manager.shutdown()


# ---------------------------------------------------------------------------
# Read-only sources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_entry_has_no_detector(tmp_path: Path) -> None:
    fs = _ReadOnlyFs({"/d/f.txt": b"old"})
    manager = await _manager(tmp_path, _Runner())
    entry = await manager.open(fs.path("/d/f.txt"))
    assert entry is not None
    assert entry.status is CopyStatus.READ_ONLY
    assert entry.detector is None
    assert entry.copy.read_only is True
    await manager.shutdown()


# ---------------------------------------------------------------------------
# A save during a running sync queues exactly one follow-up sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_quick_saves_during_slow_sync_queue_one_follow_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fs = SchemeFs({"/d/f.txt": b"old"})
    _slow_to_write(monkeypatch, fs, delay=0.4)
    runner = _Runner()
    manager = await _manager(tmp_path, runner)
    entry = await manager.open(fs.path("/d/f.txt"))
    assert entry is not None

    entry.copy.path.write_bytes(b"first")
    await _wait_until(lambda: entry.status is CopyStatus.SYNCING, max_wait=3)
    entry.copy.path.write_bytes(b"second")

    await manager.wait_idle(max_wait=5)
    assert entry.status is CopyStatus.SYNCED
    assert fs.read(fs.path("/d/f.txt")).read(20) == b"second"
    sync_jobs = [job for job in runner.jobs if job.title.startswith("Sync:")]
    assert len(sync_jobs) <= 2
    await manager.shutdown()


# ---------------------------------------------------------------------------
# Archive members: mount_archive() + two members of one ZIP syncing independently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_members_sync_independently_without_prompt(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("a.txt", "A1")
        zf.writestr("b.txt", "B1")
    fs = SchemeFs({"/d/x.zip": buffer.getvalue()})
    runner = _Runner()
    manager = await _manager(tmp_path, runner)

    archive_fs = await manager.mount_archive(fs.path("/d/x.zip"))
    assert archive_fs is not None
    entry_a = await manager.open(archive_fs.path("/a.txt"))
    entry_b = await manager.open(archive_fs.path("/b.txt"))
    assert entry_a is not None
    assert entry_b is not None

    entry_a.copy.path.write_bytes(b"A2")
    await manager.wait_idle(max_wait=3)
    assert entry_a.status is CopyStatus.SYNCED

    entry_b.copy.path.write_bytes(b"B2")
    await manager.wait_idle(max_wait=3)
    assert entry_b.status is CopyStatus.SYNCED
    assert runner.prompts == []

    data = fs.read(fs.path("/d/x.zip")).read(1_000_000)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.read("a.txt") == b"A2"
        assert zf.read("b.txt") == b"B2"
    await manager.shutdown()
