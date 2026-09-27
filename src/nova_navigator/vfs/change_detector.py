"""Detect settled content changes of one local file, robust against editor save strategies."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import watchdog.events
import watchdog.observers
from watchdog.observers.api import BaseObserver

_logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024

# Only events that can actually change the file's content or identity are worth a settle
# cycle. In particular "opened" and "closed_no_write" (read-only access, e.g. our own
# file_digest() call) must be excluded, or the detector would re-trigger itself forever:
# mark -> settle -> digest (opens+reads the file) -> opened/closed_no_write event -> mark -> ...
_FORWARDED_EVENT_TYPES = frozenset(
    {
        watchdog.events.EVENT_TYPE_MODIFIED,
        watchdog.events.EVENT_TYPE_CLOSED,
        watchdog.events.EVENT_TYPE_CREATED,
        watchdog.events.EVENT_TYPE_DELETED,
        watchdog.events.EVENT_TYPE_MOVED,
    }
)

ChangeCallback = Callable[[str], Awaitable[None]]
LogCallback = Callable[[str, str], None]


def file_digest(path: Path) -> str:
    """Return the SHA-256 hex digest of *path* using bounded reads."""
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while chunk := reader.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FileFingerprint:
    """Cheap identity of a file's current state; changes on replace, resize, or write."""

    inode: int
    size: int
    mtime_ns: int

    @classmethod
    def of(cls, path: Path) -> FileFingerprint | None:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return None
        return cls(st.st_ino, st.st_size, st.st_mtime_ns)


class _NameFilterHandler(watchdog.events.FileSystemEventHandler):
    """Forward events that concern one file name to the event loop."""

    def __init__(self, target: Path, loop: asyncio.AbstractEventLoop, mark: Callable[[str], None]) -> None:
        super().__init__()
        self._target = os.fsdecode(target)
        self._loop = loop
        self._mark = mark

    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        if event.event_type not in _FORWARDED_EVENT_TYPES:
            return
        paths = {os.fsdecode(event.src_path), os.fsdecode(getattr(event, "dest_path", "") or "")}
        if self._target in paths:
            with contextlib.suppress(RuntimeError):
                # The event loop may have been closed (e.g. detector stopped, interpreter
                # shutting down) between scheduling and this callback; the watchdog thread
                # must survive that so it can be joined cleanly by stop().
                self._loop.call_soon_threadsafe(self._mark, f"event:{event.event_type}")


class ChangeDetector:
    """Report settled content changes of *path*.

    The containing directory is watched (inode replacement by rename-over saves
    silences a watch on the file itself) and the file is also polled. Both only
    mark the file as possibly changed; a change is reported after the fingerprint
    has been stable for *settle_time* and the digest differs from the baseline.

    ``on_change`` errors are logged and do not stop detection; the reported digest
    becomes the new baseline regardless of whether ``on_change`` succeeds. Retrying
    a failed sync is the caller's responsibility, not the detector's: it reports
    settled saves only.
    """

    def __init__(
        self,
        path: Path,
        on_change: ChangeCallback,
        *,
        baseline_digest: str,
        poll_interval: float = 1.0,
        settle_time: float = 1.0,
        use_watcher: bool = True,
        log: LogCallback | None = None,
    ) -> None:
        # Resolve the parent only, not the full path: the target may not exist yet,
        # and if it is itself a symlink we still want to watch this directory/name
        # rather than follow the link to a different location.
        self._path = path.parent.resolve() / path.name
        self._on_change = on_change
        self._baseline = baseline_digest
        self._poll_interval = poll_interval
        self._settle_time = settle_time
        self._use_watcher = use_watcher
        self._log = log
        self._dirty = asyncio.Event()
        self._last_seen: FileFingerprint | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._observer: BaseObserver | None = None
        self._started = False

    @property
    def path(self) -> Path:
        return self._path

    def set_baseline(self, digest: str) -> None:
        """Treat *digest* as already reported (after a sync or a refresh)."""
        self._baseline = digest

    async def start(self) -> None:
        """Start watching; must be called from a running event loop.

        Raises:
            RuntimeError: if the detector is already started. Call :meth:`stop` first
                (a detector may be started again after being stopped).
        """
        if self._started:
            raise RuntimeError("ChangeDetector already started")
        self._started = True
        self._last_seen = FileFingerprint.of(self._path)
        if self._use_watcher:
            handler = _NameFilterHandler(self._path, asyncio.get_running_loop(), self._mark)
            observer = watchdog.observers.Observer()
            observer.schedule(handler, os.fsdecode(self._path.parent), recursive=False)
            observer.start()
            self._observer = observer
        self._tasks = [asyncio.create_task(self._poll_loop()), asyncio.create_task(self._settle_loop())]

    async def stop(self) -> None:
        """Stop watching and polling; pending unsettled changes are dropped."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        if self._observer is not None:
            observer = self._observer
            self._observer = None
            observer.stop()
            await asyncio.to_thread(observer.join)
        self._started = False

    async def check_now(self) -> None:
        """Mark the file as possibly changed, e.g. after the editor process exited."""
        self._mark("check")

    def _mark(self, reason: str) -> None:
        if self._log is not None:
            self._log("signal", reason)
        self._dirty.set()

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval)
            current = FileFingerprint.of(self._path)
            if current != self._last_seen:
                self._last_seen = current
                self._mark("poll")

    async def _settle_loop(self) -> None:
        while True:
            await self._dirty.wait()
            self._dirty.clear()
            before = FileFingerprint.of(self._path)
            await asyncio.sleep(self._settle_time)
            after = FileFingerprint.of(self._path)
            if self._dirty.is_set() or before is None or before != after:
                if self._log is not None:
                    self._log("unsettled", f"{before} -> {after}")
                self._dirty.set()
                continue
            self._last_seen = after
            try:
                digest = await asyncio.to_thread(file_digest, self._path)
            except FileNotFoundError:
                self._dirty.set()
                continue
            except Exception as exc:
                _logger.exception("ChangeDetector: failed to digest %s", self._path)
                self._report_error(exc)
                continue
            if digest == self._baseline:
                if self._log is not None:
                    self._log("unchanged", digest[:12])
                continue
            self._baseline = digest
            if self._log is not None:
                self._log("changed", digest[:12])
            try:
                await self._on_change(digest)
            except Exception as exc:
                _logger.exception("ChangeDetector: on_change callback failed for %s", self._path)
                self._report_error(exc)

    def _report_error(self, exc: Exception) -> None:
        if self._log is not None:
            self._log("error", repr(exc))
