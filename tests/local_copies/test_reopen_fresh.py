"""Reopening a changed remote file serves the new content: the fingerprint uses a fresh stat, not the listing cache."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePath
from typing import override

import pytest

from nova_navigator.local_copies.manager import CopyStatus, LocalCopyManager
from nova_navigator.nova_navigator_core import NovaNavigatorCore, PanelRef
from nova_navigator.scheduler import Job
from nova_navigator.vfs.filesystem import StreamReaderLike
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.local_copy import LocalCopy, ReuseAction
from nova_navigator.vfs.types import Stat
from nova_navigator.vfs.vpath import VPath
from nova_widgets import Response
from tests._utils.local_copy_helpers import SchemeFs, ScriptedRunner

_CONFLICT_TITLE = "Source changed since the local copy was made"


@asynccontextmanager
async def _managed(tmp_path: Path, answers: list[Response] | None = None) -> AsyncIterator[tuple[LocalCopyManager, ScriptedRunner]]:
    runner = ScriptedRunner(answers)
    manager = LocalCopyManager(tmp_path / "root", runner, poll_interval=0.05, settle_time=0.1)
    try:
        yield manager, runner
    finally:
        await manager.shutdown()


def _remote_file(tmp_path: Path, content: bytes) -> Path:
    """Create the 'remote' file (the stub server serves the local disk) and return its path."""
    directory = tmp_path / "remote"
    directory.mkdir(exist_ok=True)
    target = directory / "doc.txt"
    target.write_bytes(content)
    return target


def _warm(fs: SSHFilesystem, target: Path) -> VPath:
    """Stat *target* through the listing cache so a later out-of-band change leaves the cache stale."""
    source = VPath(target, fs)
    fs.stat(source)
    return source


def _count_reads(monkeypatch: pytest.MonkeyPatch, fs: SSHFilesystem) -> list[VPath]:
    """Record every ``read`` of *fs* and return the (live) list."""
    original = fs.read
    reads: list[VPath] = []

    def counting(path: VPath) -> StreamReaderLike:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(fs, "read", counting)
    return reads


# ---------------------------------------------------------------------------
# Reopen after an out-of-band change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reopen_after_a_larger_remote_change_serves_the_new_content(ssh_fs: SSHFilesystem, tmp_path: Path) -> None:
    target = _remote_file(tmp_path, b"old")
    source = _warm(ssh_fs, target)
    async with _managed(tmp_path) as (manager, _runner):
        entry = await manager.open(source)
        assert entry is not None
        target.write_bytes(b"a much longer new content")
        reopened = await manager.open(source)
        assert reopened is not None
        assert reopened.copy.path.read_bytes() == b"a much longer new content"


@pytest.mark.asyncio
async def test_reopen_after_a_same_size_newer_mtime_serves_the_new_content(ssh_fs: SSHFilesystem, tmp_path: Path) -> None:
    target = _remote_file(tmp_path, b"old")
    os.utime(target, (1_000_000, 1_000_000))
    source = _warm(ssh_fs, target)
    async with _managed(tmp_path) as (manager, _runner):
        entry = await manager.open(source)
        assert entry is not None
        target.write_bytes(b"new")
        os.utime(target, (2_000_000, 2_000_000))
        reopened = await manager.open(source)
        assert reopened is not None
        assert reopened.copy.path.read_bytes() == b"new"


@pytest.mark.asyncio
async def test_reopen_of_an_unchanged_file_reuses_the_copy_without_downloading(ssh_fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _remote_file(tmp_path, b"same")
    source = _warm(ssh_fs, target)
    reads = _count_reads(monkeypatch, ssh_fs)
    async with _managed(tmp_path) as (manager, _runner):
        entry = await manager.open(source)
        assert entry is not None
        assert len(reads) == 1
        reopened = await manager.open(source)
        assert reopened is entry
        assert len(reads) == 1
        assert entry.copy.path.read_bytes() == b"same"


@pytest.mark.asyncio
async def test_reopen_leaves_the_panel_listing_cache_untouched(gnu_fs: SSHFilesystem, tmp_path: Path) -> None:
    target = _remote_file(tmp_path, b"old")
    source = _warm(gnu_fs, target)
    async with _managed(tmp_path) as (manager, _runner):
        assert await manager.open(source) is not None
        target.write_bytes(b"a much longer new content")
        before = dict(gnu_fs._stat_cache)
        assert before
        assert await manager.open(source) is not None
        assert gnu_fs._stat_cache == before


@pytest.mark.asyncio
async def test_same_size_change_within_the_same_second_is_not_detected_documented_limit(ssh_fs: SSHFilesystem, tmp_path: Path) -> None:
    # Documented limit (docs/local_copies.md, Limits): the fingerprint is size and whole-second mtime,
    # so a same-size rewrite within the same second looks unchanged and the old copy is reused.
    target = _remote_file(tmp_path, b"old")
    os.utime(target, (1_000_000, 1_000_000))
    source = _warm(ssh_fs, target)
    async with _managed(tmp_path) as (manager, _runner):
        entry = await manager.open(source)
        assert entry is not None
        target.write_bytes(b"new")
        os.utime(target, (1_000_000, 1_000_000))
        reopened = await manager.open(source)
        assert reopened is entry
        assert entry.copy.path.read_bytes() == b"old"


# ---------------------------------------------------------------------------
# Sync back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_back_after_an_out_of_band_change_asks_and_skip_keeps_the_remote_content(ssh_fs: SSHFilesystem, tmp_path: Path) -> None:
    target = _remote_file(tmp_path, b"old")
    source = _warm(ssh_fs, target)
    async with _managed(tmp_path, [Response.SKIP]) as (manager, runner):
        entry = await manager.open(source)
        assert entry is not None
        target.write_bytes(b"out of band content")
        entry.copy.path.write_bytes(b"my local edit")
        await manager.wait_idle(max_wait=3)
        assert runner.prompts == [_CONFLICT_TITLE]
        assert target.read_bytes() == b"out of band content"
        assert entry.status is CopyStatus.CONFLICT


@pytest.mark.asyncio
async def test_sync_back_without_a_remote_change_does_not_prompt(gnu_fs: SSHFilesystem, tmp_path: Path) -> None:
    target = _remote_file(tmp_path, b"old")
    source = _warm(gnu_fs, target)
    async with _managed(tmp_path) as (manager, runner):
        entry = await manager.open(source)
        assert entry is not None
        entry.copy.path.write_bytes(b"first edit")
        await manager.wait_idle(max_wait=3)
        assert target.read_bytes() == b"first edit"
        entry.copy.path.write_bytes(b"second edit, longer")
        await manager.wait_idle(max_wait=3)
        assert target.read_bytes() == b"second edit, longer"
        assert runner.prompts == []
        assert entry.status is CopyStatus.SYNCED


# ---------------------------------------------------------------------------
# First open, wrapper, open-with path, task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_open_after_an_out_of_band_change_records_a_fresh_baseline(gnu_fs: SSHFilesystem, tmp_path: Path) -> None:
    target = _remote_file(tmp_path, b"old")
    source = _warm(gnu_fs, target)
    target.write_bytes(b"a much longer new content")
    async with _managed(tmp_path) as (manager, _runner):
        entry = await manager.open(source)
        assert entry is not None
        assert entry.copy.baseline.source.size == len(b"a much longer new content")
        assert entry.copy.path.read_bytes() == b"a much longer new content"


@pytest.mark.asyncio
async def test_remote_scheme_wrapper_reopen_serves_the_new_content(gnu_fs: SSHFilesystem, tmp_path: Path) -> None:
    target = _remote_file(tmp_path, b"old")
    wrapper = RemoteFilesystem("prod", gnu_fs)
    source = wrapper.path(str(target))
    wrapper.stat(source)  # warm the inner listing cache
    async with _managed(tmp_path) as (manager, _runner):
        entry = await manager.open(source)
        assert entry is not None
        target.write_bytes(b"a much longer new content")
        reopened = await manager.open(source)
        assert reopened is not None
        assert reopened.copy.path.read_bytes() == b"a much longer new content"


class _OpenWithCore(NovaNavigatorCore):
    """The real ``open_path`` over a real manager; ``execute_command`` records the opened copy's content."""

    def __init__(self, manager: LocalCopyManager) -> None:
        super().__init__()
        self.local_copies = manager
        self.opened_content: list[bytes] = []

    async def open_editor(self, path: VPath) -> None:
        pass

    async def start_job(self, job: Job) -> None:
        raise AssertionError("the manager's own runner starts the jobs")

    async def execute_command(self, args: list[str], cwd: PurePath) -> None:
        self.opened_content.append((Path(cwd) / "doc.txt").read_bytes())

    async def set_terminal_directory(self, path: VPath) -> None:
        pass

    async def set_panel_directory(self, path: VPath, panel: PanelRef) -> None:
        pass


@pytest.mark.asyncio
async def test_open_path_reopens_a_changed_remote_file_with_the_new_content(gnu_fs: SSHFilesystem, tmp_path: Path) -> None:
    target = _remote_file(tmp_path, b"old")
    source = _warm(gnu_fs, target)
    async with _managed(tmp_path) as (manager, _runner):
        core = _OpenWithCore(manager)
        await core.open_path(source)
        target.write_bytes(b"a much longer new content")
        await core.open_path(source)
        assert core.opened_content == [b"old", b"a much longer new content"]


class _CountingStatFs(SchemeFs):
    """SchemeFs that counts the cached ``stat`` calls it receives."""

    def __init__(self, files: dict[str, bytes | None]) -> None:
        super().__init__(files)
        self.stat_calls = 0

    @override
    def stat(self, path: VPath) -> Stat:
        self.stat_calls += 1
        return super().stat(path)

    @override
    def stat_uncached(self, path: VPath) -> Stat:
        return super().stat(path)  # the real answer, without counting it as a cached call


@pytest.mark.asyncio
async def test_create_copy_task_makes_no_cached_stat_call(tmp_path: Path) -> None:
    fs = _CountingStatFs({"/d/f.txt": b"hello"})
    async with _managed(tmp_path) as (manager, _runner):
        entry = await manager.open(fs.path("/d/f.txt"))
        assert entry is not None
        assert fs.stat_calls == 0


# ---------------------------------------------------------------------------
# Local files stay a pass-through
# ---------------------------------------------------------------------------


def test_local_file_open_is_unchanged_pass_through(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_bytes(b"one")
    copy = LocalCopy.create(LocalFilesystem.singleton().path(real), tmp_path / "root")
    assert copy.is_pass_through
    assert copy.path == real
    assert copy.reuse_action() is ReuseAction.REUSE
    assert not copy.source_changed()
    real.write_bytes(b"a longer rewrite")
    assert copy.source_changed()
