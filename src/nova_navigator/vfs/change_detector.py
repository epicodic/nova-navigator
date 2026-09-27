"""Detect settled content changes of one local file, robust against editor save strategies."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import watchdog.events
import watchdog.observers
from watchdog.observers.api import BaseObserver

_CHUNK_SIZE = 1024 * 1024

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
        paths = {os.fsdecode(event.src_path), os.fsdecode(getattr(event, "dest_path", "") or "")}
        if self._target in paths:
            self._loop.call_soon_threadsafe(self._mark, f"event:{event.event_type}")


class ChangeDetector:
    """Report settled content changes of *path*.

    The containing directory is watched (inode replacement by rename-over saves
    silences a watch on the file itself) and the file is also polled. Both only
    mark the file as possibly changed; a change is reported after the fingerprint
    has been stable for *settle_time* and the digest differs from the baseline.
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
        self._path = path
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

    @property
    def path(self) -> Path:
        return self._path

    def set_baseline(self, digest: str) -> None:
        """Treat *digest* as already reported (after a sync or a refresh)."""
        self._baseline = digest

    async def start(self) -> None:
        """Start watching; must be called from a running event loop."""
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
            if digest == self._baseline:
                if self._log is not None:
                    self._log("unchanged", digest[:12])
                continue
            self._baseline = digest
            if self._log is not None:
                self._log("changed", digest[:12])
            await self._on_change(digest)
