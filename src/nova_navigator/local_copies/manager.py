"""Registry of open local copies; starts open and sync jobs and reacts to detected saves.

Runs entirely on the GUI event loop: the registry (``self._entries``) is mutated only
here, never from a worker thread. ``open()``, ``sync_now()``, and ``mount_archive()``
start :class:`~nova_navigator.scheduler.Job` instances, whose task functions (in
``local_copies/tasks.py``) run the blocking :class:`LocalCopy` I/O in a worker thread and
are always awaited to completion before this class touches its state again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from nova_navigator.archive.archives import is_archive_writable
from nova_navigator.archive.backing import CopiedArchiveBacking
from nova_navigator.scheduler import Job
from nova_navigator.vfs.change_detector import ChangeDetector
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.local_copy import LocalCopy
from nova_navigator.vfs.vpath import VPath

from .tasks import TaskResult, create_copy_task, reopen_copy_task, sync_copy_task

_logger = logging.getLogger(__name__)

JobStarter = Callable[[Job], Awaitable[None]]


class CopyStatus(StrEnum):
    """Status of one open local copy, as shown in the Local Copies dialog."""

    SYNCED = "synced"
    MODIFIED = "modified"
    SYNCING = "syncing"
    FAILED = "failed"
    CONFLICT = "conflict"
    READ_ONLY = "read-only"


def _key(source: VPath) -> str:
    """Registry key for *source*: its URI, or ``archive-uri#member-path`` for a member."""
    filesystem = source.filesystem
    if isinstance(filesystem, ArchiveFilesystem):
        return f"{filesystem.source.uri}#{source.path.as_posix()}"
    return source.uri


@dataclass(eq=False)
class CopyEntry:
    """One open local copy: its status, change detector, and any in-flight sync."""

    copy: LocalCopy
    status: CopyStatus
    error: str | None = None
    detector: ChangeDetector | None = None
    sync_queued: bool = False
    sync_task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def key(self) -> str:
        """Registry key this entry is (or was) filed under."""
        return _key(self.copy.source)

    @property
    def has_unsynced_changes(self) -> bool:
        """True if the status alone indicates changes not yet safely on the source."""
        return self.status in {CopyStatus.MODIFIED, CopyStatus.FAILED, CopyStatus.CONFLICT, CopyStatus.SYNCING}


class LocalCopyManager:
    """Registry of open local copies, their change detectors, and their open/sync jobs."""

    def __init__(self, root: Path, start_job: JobStarter, *, poll_interval: float = 1.0, settle_time: float = 1.0) -> None:
        self._root = root
        self._start_job = start_job
        self._poll_interval = poll_interval
        self._settle_time = settle_time
        self._entries: dict[str, CopyEntry] = {}
        # Archive files mounted through mount_archive(), keyed by source URI. Reusing the
        # same ArchiveFilesystem instance on a second mount of the same archive matters
        # because sibling-member sync rebasing matches archive filesystems by `is`-identity
        # (see _rebase_siblings) -- a fresh instance would defeat it even if it wrapped the
        # very same local file.
        self._archive_mounts: dict[str, ArchiveFilesystem] = {}
        self._shutdown_started = False

    @property
    def entries(self) -> list[CopyEntry]:
        """Open copies in the order they were opened."""
        return list(self._entries.values())

    # -- Open / reopen ----------------------------------------------------

    async def open(self, source: VPath) -> CopyEntry | None:
        """Open a local copy of *source*, creating or reusing one, and start watching it.

        Returns None if the open job did not complete: cancelled, failed, or (when
        reopening) the user chose Cancel on a conflict. An existing entry is left
        untouched in that case.
        """
        key = _key(source)
        entry = self._entries.get(key)
        if entry is not None:
            return await self._reopen(entry)
        read_only = source.filesystem.capabilities.read_only
        result = TaskResult()
        job = Job(f"Open: {source.name}", create_copy_task, source, self._root, read_only, result)
        await self._start_job(job)
        if not result.opened or job.state is not Job.State.COMPLETED or result.copy is None:
            return None
        copy = result.copy
        entry = CopyEntry(copy=copy, status=CopyStatus.READ_ONLY if copy.read_only else CopyStatus.SYNCED)
        if not copy.read_only:
            entry.detector = self._make_detector(entry)
            await entry.detector.start()
        self._entries[key] = entry
        return entry

    async def _reopen(self, entry: CopyEntry) -> CopyEntry | None:
        result = TaskResult()
        job = Job(f"Open: {entry.copy.source.name}", reopen_copy_task, entry.copy, result)
        await self._start_job(job)
        if not result.opened or job.state is not Job.State.COMPLETED:
            return None
        entry.status = CopyStatus.READ_ONLY if entry.copy.read_only else CopyStatus.SYNCED
        entry.error = None
        if not entry.copy.read_only:
            if entry.detector is None:
                entry.detector = self._make_detector(entry)
                await entry.detector.start()
            else:
                entry.detector.set_baseline(entry.copy.baseline.digest)
        return entry

    def _make_detector(self, entry: CopyEntry) -> ChangeDetector:
        return ChangeDetector(
            entry.copy.path,
            lambda _digest: self._on_change(entry),
            baseline_digest=entry.copy.baseline.digest,
            poll_interval=self._poll_interval,
            settle_time=self._settle_time,
        )

    # -- Sync ---------------------------------------------------------------

    async def _on_change(self, entry: CopyEntry) -> None:
        """React to a settled local edit; syncing stays paused while the entry is in conflict.

        A change detected while a sync is already running (status SYNCING) only queues a
        follow-up; it must not overwrite the SYNCING status with MODIFIED, since a sync
        really is in progress right now.
        """
        if entry.status is CopyStatus.CONFLICT:
            return
        if entry.status is not CopyStatus.SYNCING:
            entry.status = CopyStatus.MODIFIED
        self._schedule_sync(entry, force=False)

    def _schedule_sync(self, entry: CopyEntry, force: bool) -> None:
        if entry.sync_task is not None and not entry.sync_task.done():
            entry.sync_queued = True
            return
        task = asyncio.create_task(self._run_sync(entry, force))
        task.add_done_callback(self._log_task_exception)
        entry.sync_task = task

    def _log_task_exception(self, task: asyncio.Task[None]) -> None:
        """Surface an unexpected exception from a sync task instead of letting it vanish."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _logger.error("Unhandled exception in local-copy sync task", exc_info=exc)

    async def _run_sync(self, entry: CopyEntry, force: bool) -> None:
        while True:
            entry.status = CopyStatus.SYNCING
            result = TaskResult()
            job = Job(f"Sync: {entry.copy.source.name}", sync_copy_task, entry.copy, force, result)
            await self._start_job(job)
            entry.error = None

            if result.conflict:
                # A conflict pauses automatic syncing until a manual sync_now(); any change
                # queued while the prompt was pending must not trigger another sync loop --
                # it stays folded into the paused copy's next manual sync.
                entry.status = CopyStatus.CONFLICT
                entry.sync_queued = False
                break

            if job.state is Job.State.COMPLETED:
                if entry.detector is not None:
                    entry.detector.set_baseline(entry.copy.baseline.digest)
                await self._rebase_siblings(entry)
            else:
                entry.status = CopyStatus.FAILED
                entry.error = job.error or f"Sync ended in unexpected state: {job.state.name}"

            if not entry.sync_queued:
                # Only report SYNCED once the loop is truly done; a queued follow-up means
                # another edit is already waiting, so the copy is not actually settled yet.
                if job.state is Job.State.COMPLETED:
                    entry.status = CopyStatus.SYNCED
                break
            entry.sync_queued = False
            force = False
        entry.sync_task = None

    async def _rebase_siblings(self, entry: CopyEntry) -> None:
        """Let sibling copies from the same archive absorb its new fingerprint.

        The registry (self._entries) is only ever read or mutated on the GUI loop, so the
        sibling LocalCopy list is snapshotted here before handing it to a worker thread;
        only the blocking rebase_container() calls themselves run off the GUI loop.
        """
        filesystem = entry.copy.source.filesystem
        if not isinstance(filesystem, ArchiveFilesystem):
            return
        siblings = [other.copy for other in self._entries.values() if other is not entry and other.copy.source.filesystem is filesystem]
        if siblings:
            await asyncio.to_thread(self._rebase_copies, siblings)

    @staticmethod
    def _rebase_copies(copies: list[LocalCopy]) -> None:
        for copy in copies:
            try:
                copy.rebase_container()
            except Exception:
                _logger.exception("Failed to rebase sibling archive member %s", copy.source.uri)

    async def sync_now(self, entry: CopyEntry) -> None:
        """Force a sync now; a CONFLICT status means "I checked, overwrite the source"."""
        self._schedule_sync(entry, force=entry.status is CopyStatus.CONFLICT)
        task = entry.sync_task
        if task is not None:
            await task

    async def check_now(self, entry: CopyEntry) -> None:
        """Ask the entry's detector to check for changes now, e.g. after an editor process exits."""
        if entry.detector is not None:
            await entry.detector.check_now()

    # -- Close / discard / bulk ----------------------------------------------

    async def release(self, entry: CopyEntry) -> None:
        """End a built-in editor session on *entry*: stop watching, wait for a running sync, then sync a modified copy once.

        Unlike :meth:`close`, the detector is stopped first, so a save that settles during the final sync cannot
        queue a second identical upload, and an entry in ``CONFLICT`` is left alone instead of being overwritten
        without asking. A failed sync leaves the status ``FAILED`` with the copy's changes kept. Safe to call twice.
        """
        if entry.detector is not None:
            await entry.detector.stop()
            entry.detector = None
        task = entry.sync_task
        if task is not None and not task.done():
            await asyncio.wait({task})
        if entry.copy.read_only or entry.status is CopyStatus.CONFLICT:
            return
        if entry.copy.is_modified():
            await self.sync_now(entry)

    async def close(self, entry: CopyEntry) -> None:
        """Sync unsynced changes, then stop watching; the local file stays for fast reuse."""
        if not entry.copy.read_only and entry.copy.is_modified():
            await self.sync_now(entry)
        if entry.detector is not None:
            await entry.detector.stop()
            entry.detector = None

    async def discard(self, entry: CopyEntry) -> None:
        """Delete the local file and drop the entry; confirming any unsynced loss is the UI's job."""
        if entry.detector is not None:
            await entry.detector.stop()
            entry.detector = None
        if entry.sync_task is not None:
            with contextlib.suppress(Exception):
                await entry.sync_task
        entry.copy.discard()
        self._entries.pop(entry.key, None)

    def unsynced(self) -> list[CopyEntry]:
        """Entries with changes not safely on the source: by status, or an unsynced writable copy."""
        return [e for e in self._entries.values() if e.has_unsynced_changes or (not e.copy.read_only and e.copy.is_modified())]

    async def wait_idle(self, max_wait: float) -> None:
        """Test/quit helper: wait for detectors to settle and all sync tasks to finish."""
        await asyncio.sleep(2 * (self._settle_time + self._poll_interval))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_wait
        while any(e.sync_task is not None and not e.sync_task.done() for e in self._entries.values()):
            if loop.time() >= deadline:
                return
            await asyncio.sleep(0.05)

    async def shutdown(self, remove_files: bool = True) -> None:
        """Stop all detectors, await any running syncs, close cached archive mounts, and optionally remove the local-copy root.

        Idempotent: a first call decides *remove_files* for the whole process, and every later
        call (e.g. the app's unconditional cleanup on unmount, after a quit prompt already shut
        things down explicitly) is a no-op, so an earlier "keep files" decision is never undone
        by a later default-argument call.
        """
        if self._shutdown_started:
            return
        self._shutdown_started = True
        for entry in self._entries.values():
            if entry.detector is not None:
                await entry.detector.stop()
                entry.detector = None
            if entry.sync_task is not None:
                with contextlib.suppress(Exception):
                    await entry.sync_task
        self._entries.clear()
        for archive_fs in self._archive_mounts.values():
            archive_fs.close()
        self._archive_mounts.clear()
        if remove_files:
            await asyncio.to_thread(shutil.rmtree, self._root, ignore_errors=True)

    # -- Archives -------------------------------------------------------------

    async def mount_archive(self, source: VPath) -> ArchiveFilesystem | None:
        """Mount *source* as an archive filesystem, downloading it locally first if not local.

        Design decision: a local archive is mounted directly, matching
        :meth:`LocalCopy.create`'s own pass-through for local sources -- no LocalCopy, no
        entry. Everything else gets a LocalCopy of the archive file, cached by source URI:
        mounting the same archive again while that copy is still fresh reuses the very same
        ArchiveFilesystem instance rather than re-downloading and re-mounting it (see the
        ``_archive_mounts`` comment above for why the instance itself must be reused, not
        just the copy). A cached mount is refreshed through
        :meth:`ArchiveFilesystem.refresh_from_source`, which serializes with any concurrent
        member commit via the mount's own write lock (both mutate the same local archive
        file) and only re-downloads when the source actually changed. There is no
        interactive session on the archive file itself, so unlike ``open()`` a conflict here
        has nothing worth prompting about; it is resolved by keeping the source's content,
        same as a plain REFRESH.
        """
        if isinstance(source.filesystem.unwrap(), LocalFilesystem):
            return ArchiveFilesystem(source.parent, source)

        key = source.uri
        cached = self._archive_mounts.get(key)
        if cached is not None:
            await asyncio.to_thread(cached.refresh_from_source)
            return cached

        read_only = not is_archive_writable(source.path) or source.filesystem.capabilities.read_only
        result = TaskResult()
        job = Job(f"Open: {source.name}", create_copy_task, source, self._root, read_only, result)
        await self._start_job(job)
        if not result.opened or job.state is not Job.State.COMPLETED or result.copy is None:
            return None
        # The rebuilt archive is renamed into the copy, so its work dir must share the copy's root (same device).
        archive_fs = ArchiveFilesystem(source.parent, CopiedArchiveBacking(result.copy), work_dir=self._root / "work")
        self._archive_mounts[key] = archive_fs
        return archive_fs
