from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from io import BufferedReader, BufferedWriter
from pathlib import PurePath
from stat import S_IMODE, S_ISDIR, S_ISLNK
from typing import Any, override

import watchdog.events
import watchdog.observers

from ..filesystem import Filesystem, FilesystemCapabilities, Stat, StreamReaderLike, StreamWriterLike
from ..vpath import VPath

logging.getLogger("watchdog").setLevel(logging.WARNING)


class LocalFilesystem(Filesystem):
    """Filesystem implementation for the local operating-system filesystem.

    A singleton (:meth:`singleton`) is provided for the common case where a
    single process-wide instance is sufficient.
    """

    _singleton: LocalFilesystem | None = None

    @staticmethod
    def singleton() -> LocalFilesystem:
        """Return the process-wide singleton :class:`LocalFilesystem` instance."""
        if LocalFilesystem._singleton is None:
            LocalFilesystem._singleton = LocalFilesystem()
        return LocalFilesystem._singleton

    def __eq__(self, value: object) -> bool:
        return isinstance(value, LocalFilesystem)

    def __hash__(self) -> int:
        return hash("LocalFilesystem")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}"

    @property
    @override
    def capabilities(self) -> FilesystemCapabilities:
        return FilesystemCapabilities(
            streaming_iterdir=True,
            watch=True,
            symlinks=True,
            permissions=True,
        )

    @override
    def is_same_device(self, path1: VPath, path2: VPath) -> bool:
        self._assert_vpath(path1)
        if not isinstance(path2.filesystem, LocalFilesystem):
            return False
        # Use os.stat to get device IDs and compare them
        try:
            stat1 = os.stat(path1.path)
            stat2 = os.stat(path2.path)
            return stat1.st_dev == stat2.st_dev
        except (FileNotFoundError, OSError):
            return False

    @override
    def path(self, p: str | PurePath) -> VPath:
        return VPath(os.path.expanduser(str(p)), self)

    @override
    def cwd(self) -> VPath:
        return VPath(os.getcwd(), self)

    @override
    def root(self) -> VPath:
        return VPath("/", self)

    @override
    def home(self) -> VPath:
        return VPath(os.path.expanduser("~"), self)

    @override
    async def iterdir(
        self,
        path: VPath,
        *,
        cancel: threading.Event | None = None,
    ) -> AsyncIterator[VPath]:
        self._assert_vpath(path)
        with os.scandir(path.path) as scanner:
            for entry in scanner:
                if cancel is not None and cancel.is_set():
                    return
                vp = VPath(entry.path, self)
                try:
                    lstat = entry.stat(follow_symlinks=False)
                    try:
                        fstat = entry.stat(follow_symlinks=True)
                        is_broken_symlink = False
                    except FileNotFoundError:
                        fstat = lstat
                        is_broken_symlink = True
                    is_hidden = entry.name.startswith(".") if os.name != "nt" else bool(lstat.st_file_attributes & 0x2)
                    vp._stat = Stat(
                        size=fstat.st_size,
                        modified=fstat.st_mtime,
                        mode=S_IMODE(lstat.st_mode),
                        is_hidden=is_hidden,
                        is_directory=S_ISDIR(fstat.st_mode),
                        is_executable=fstat.st_mode & 0o111 != 0,
                        is_symlink=S_ISLNK(lstat.st_mode),
                        is_broken_symlink=is_broken_symlink,
                    )
                except OSError:
                    vp._stat = Stat()
                yield vp

    @asynccontextmanager
    @override
    async def watch(
        self,
        path: VPath,
        callback: Callable[[VPath], Awaitable[None]],
    ) -> AsyncIterator[None]:
        """Watch *path* for changes using watchdog (inotify on Linux).

        Debounces rapid change events with a 0.2 s timer.
        The callback is scheduled on the running event loop from the watchdog thread.
        """
        self._assert_vpath(path)
        loop = asyncio.get_running_loop()

        class _DebounceHandler(watchdog.events.FileSystemEventHandler):
            _DEBOUNCE_INTERVAL: float = 0.2
            _timer: threading.Timer | None = None
            _stopped: bool = False
            _lock: threading.Lock

            def __init__(self) -> None:
                super().__init__()
                self._lock = threading.Lock()

            def _fire(self) -> None:
                with self._lock:
                    if self._stopped:
                        return

                async def _invoke() -> None:
                    await callback(path)

                asyncio.run_coroutine_threadsafe(_invoke(), loop)

            def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
                if not isinstance(event, watchdog.events.DirModifiedEvent):
                    return
                with self._lock:
                    if self._timer is not None:
                        self._timer.cancel()
                    self._timer = threading.Timer(self._DEBOUNCE_INTERVAL, self._fire)
                    self._timer.start()

            def stop(self) -> None:
                with self._lock:
                    self._stopped = True
                    if self._timer is not None:
                        self._timer.cancel()
                        self._timer = None

        handler = _DebounceHandler()
        observer = watchdog.observers.Observer()
        observer.start()
        watch_handle = observer.schedule(handler, path.path.as_posix(), recursive=False)
        try:
            yield
        finally:
            handler.stop()
            observer.unschedule(watch_handle)
            observer.stop()
            observer.join()

    @override
    def parent(self, path: VPath) -> VPath:
        self._assert_vpath(path)
        return VPath(path.path.parent, self)

    @override
    def stat(self, path: VPath) -> Stat:
        self._assert_vpath(path)
        lstat = os.stat(path, follow_symlinks=False)

        try:
            stat = os.stat(path, follow_symlinks=True)
            is_broken_symlink = False
        except FileNotFoundError:
            stat = lstat
            is_broken_symlink = True

        if os.name != "nt":
            is_hidden = path.name.startswith(".")
        else:
            is_hidden = stat.st_file_attributes & 0x2  # = FILE_ATTRIBUTE_HIDDEN

        return Stat(
            size=stat.st_size,
            modified=stat.st_mtime,
            mode=S_IMODE(lstat.st_mode),
            is_hidden=is_hidden,
            is_directory=S_ISDIR(stat.st_mode),
            is_executable=stat.st_mode & 0o111 != 0,
            is_symlink=S_ISLNK(lstat.st_mode),
            is_broken_symlink=is_broken_symlink,
        )

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        self._assert_vpath(path)

        class StreamReaderWrapper:
            def __init__(self, f: Any) -> None:
                assert isinstance(f, BufferedReader)
                self._f = f

            def read(self, size: int) -> bytes:
                return self._f.read(size)

            def close(self) -> None:
                self._f.close()

        return StreamReaderWrapper(open(path.path, mode="rb"))

    @override
    def write(self, path: VPath) -> StreamWriterLike:
        self._assert_vpath(path)

        class StreamWriterWrapper:
            def __init__(self, f: Any) -> None:
                assert isinstance(f, BufferedWriter)
                self._f = f

            def write(self, data: bytes) -> int:
                return self._f.write(data)

            def close(self) -> None:
                self._f.close()

        os.makedirs(path.path.parent, exist_ok=True)
        return StreamWriterWrapper(open(path.path, "wb"))

    @override
    def remove(self, path: VPath) -> None:
        self._assert_vpath(path)
        os.remove(path.path)

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        self._assert_vpath(src_path)
        self._assert_vpath(dst_path)
        os.rename(src_path.path, dst_path.path)

    @override
    def rmdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        os.rmdir(path.path)

    @override
    def mkdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        os.mkdir(path.path)

    @override
    def copy_stat(self, path: VPath, stat: Stat) -> None:
        self._assert_vpath(path)
        p = path.path
        if stat.modified >= 0:
            os.utime(p, (stat.modified, stat.modified), follow_symlinks=False)
        if stat.mode >= 0:
            os.chmod(p, stat.mode, follow_symlinks=False)

    @override
    def readlink(self, path: VPath) -> str:
        self._assert_vpath(path)
        return os.readlink(path.path)

    @override
    def refresh(self, path: VPath | None = None) -> None:
        pass  # no caching in LocalFilesystem
