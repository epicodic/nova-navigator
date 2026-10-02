"""Save support of the core: change detection of the file behind a document (REQ-14) and the streaming atomic `SaveJob`.

`ChangeKind`, `FileIdentity` and `SourceChanged` live in `byte_source` (the source needs them and `save` imports the source); they are
re-exported here. This module imports only `core` modules.
"""

from __future__ import annotations

import atexit
import contextlib
import errno
import os
import stat
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol

from nova_editor.core.byte_source import ByteSource, ChangeKind, FileIdentity, PreadSource, SourceChanged
from nova_editor.core.line_index import DEFAULT_LONG_LINE_CAP, DEFAULT_LONG_LINE_THRESHOLD, DEFAULT_STRIDE, LineIndex
from nova_editor.core.row_scanner import LongRow, RowScanner
from nova_editor.core.save_layout import SaveLayout

__all__ = [
    "CHUNK",
    "FSYNC_EVERY",
    "ChangeKind",
    "FileIdentity",
    "PauseGate",
    "PlanPart",
    "SaveCancelled",
    "SaveFailed",
    "SaveIo",
    "SaveJob",
    "SavePlanner",
    "SaveProgress",
    "SaveResult",
    "SaveSettings",
    "check_path",
]

CHUNK = 1024 * 1024
FSYNC_EVERY = 256 * 1024 * 1024
PROGRESS_INTERVAL = 1 / 20
NAME_BYTES = 100
UNSUPPORTED = {errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL, errno.ENOSYS}
NO_SPACE = {errno.ENOSPC, errno.EDQUOT}

_UMASK_LOCK = threading.Lock()
_TEMP_LOCK = threading.Lock()
_TEMP_NAMES: set[str] = set()  # guarded by _TEMP_LOCK


def check_path(path: Path | str, held: FileIdentity | None) -> ChangeKind:
    """Compare the file at `path` with the identity `held` since open time using one `os.stat` of the resolved path.

    `held=None` means "expected new": an existing file is `CREATED`, a missing one `UNCHANGED`.
    A directory in the way, a permission error or any other `OSError` is `UNREADABLE`.
    """
    try:
        info = os.stat(os.path.realpath(path))
    except FileNotFoundError:
        return ChangeKind.DELETED if held is not None else ChangeKind.UNCHANGED
    except OSError:
        return ChangeKind.UNREADABLE
    if held is None:
        return ChangeKind.CREATED
    if (info.st_dev, info.st_ino) != (held.dev, held.ino):
        return ChangeKind.REPLACED
    if info.st_size < held.size:
        return ChangeKind.TRUNCATED
    if info.st_size != held.size or info.st_mtime_ns != held.mtime_ns:
        return ChangeKind.MODIFIED
    return ChangeKind.UNCHANGED


@dataclass(frozen=True)
class SaveSettings:
    """Tunables of a save: chunk size, fsync interval, preallocation and the settings of the new line index."""

    chunk: int = CHUNK
    fsync_every: int = FSYNC_EVERY
    preallocate: bool = True
    build_index: bool = True
    stride: int = DEFAULT_STRIDE
    long_line_threshold: int = DEFAULT_LONG_LINE_THRESHOLD
    long_line_cap: int = DEFAULT_LONG_LINE_CAP


@dataclass(frozen=True)
class SaveProgress:
    """Progress of a save; `phase` is `writing`, `flushing`, `history` or `finishing`."""

    phase: str
    done: int
    total: int


class PlanPart(NamedTuple):
    """A range `[a, b)` of the byte source `source`, numbered `src` (0 is the original file)."""

    src: int
    source: ByteSource
    a: int
    b: int


class SavePlanner(Protocol):
    """What the job needs from the document: its length and the parts that make up a byte range of it."""

    def length(self) -> int:
        """Return the length of the document in bytes."""
        ...

    def plan(self, offset: int, limit: int, unverified: bool) -> list[PlanPart]:
        """Return the parts covering `[offset, offset + limit)` of the document, in order."""
        ...


class PauseGate(Protocol):
    """The part of `Foreground` the job uses to give way to the UI."""

    def pause_seconds(self) -> float:
        """Return how long to sleep before the next piece of work."""
        ...


class SaveFailed(OSError):
    """The save failed at `stage` (`prepare`, `write`, `flush`, `replace` or `internal`); the errno of the cause is kept.

    `committed` is true when the replace already happened: the target holds the new bytes but the editor could not switch to the new file.
    """

    def __init__(self, stage: str, error: OSError | str, *, committed: bool = False) -> None:
        if isinstance(error, OSError):
            super().__init__(error.errno, error.strerror or str(error))
        else:
            super().__init__(error)
        self.stage = stage
        self.committed = committed


class SaveCancelled(Exception):
    """The save was cancelled before the commit point."""


@dataclass
class SaveResult:
    """The outcome of a successful save."""

    target: Path
    length: int
    source: PreadSource
    line_index: LineIndex | None
    layout: SaveLayout
    mode: int


def _read_umask() -> int:
    with _UMASK_LOCK:  # there is no read-only call: set it to 0 and restore it
        mask = os.umask(0)
        os.umask(mask)
    return mask


def _open_ro(path: str) -> int:
    return os.open(path, os.O_RDONLY)


def _fsync_dir(path: str) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass
class SaveIo:
    """The file operations of a save; the defaults call `os`, tests pass instances that fail at the n-th call."""

    mkstemp: Callable[..., tuple[int, str]] = tempfile.mkstemp
    fallocate: Callable[[int, int, int], None] = os.posix_fallocate
    write: Callable[[int, bytes | memoryview], int] = os.write
    fsync: Callable[[int], None] = os.fsync
    fchmod: Callable[[int, int], None] = os.fchmod
    open_ro: Callable[[str], int] = _open_ro
    replace: Callable[[str, str], None] = os.replace
    unlink: Callable[[str], None] = os.unlink
    fsync_dir: Callable[[str], None] = _fsync_dir
    umask: Callable[[], int] = _read_umask


def _forget_temp(name: str) -> None:
    with _TEMP_LOCK:
        _TEMP_NAMES.discard(name)


def _cleanup_temp_files() -> None:
    with _TEMP_LOCK:
        names = list(_TEMP_NAMES)
        _TEMP_NAMES.clear()
    for name in names:
        with contextlib.suppress(OSError):
            os.unlink(name)


atexit.register(_cleanup_temp_files)


class SaveJob:
    """Write the planned document to a temp file next to `target`, flush it and atomically replace `target` (design section 4.3).

    `run()` has exactly one outcome: a `SaveResult`, `SaveCancelled` (before the commit point) or `SaveFailed`.
    Before the replace the original stays byte for byte unchanged and no temp file is left.
    """

    def __init__(
        self,
        planner: SavePlanner,
        target: Path | str,
        settings: SaveSettings,
        progress: Callable[[SaveProgress], None] | None,
        foreground: PauseGate | None,
        unverified: bool = False,
        io: SaveIo | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the job.

        Args:
            planner: Supplies the length of the document and the parts of each chunk.
            target: The file to replace or create; symlinks are resolved.
            settings: Chunk size, fsync interval and index settings.
            progress: Called from the job's thread with each report, or `None`.
            foreground: Gate asked for a pause after every chunk, or `None`.
            unverified: Passed to the planner so that it reads the original without the size and mtime check.
            io: The file operations; `None` uses `os`.
            clock: Time source of the progress throttle.
        """
        self._planner = planner
        self._target = Path(target)
        self._settings = settings
        self._progress = progress
        self._foreground = foreground
        self._unverified = unverified
        self._io = io if io is not None else SaveIo()
        self._clock = clock
        self._cancelled = threading.Event()
        self._layout = SaveLayout()
        self._last_report: float | None = None

    def cancel(self) -> None:
        """Ask the job to stop; it raises `SaveCancelled` at the next check unless the replace already happened."""
        self._cancelled.set()

    def run(self) -> SaveResult:
        """Run the save on the calling thread."""
        try:
            return self._run()
        except (SaveFailed, SaveCancelled):
            raise
        except OSError as error:
            raise SaveFailed("internal", error) from error
        except Exception as error:
            raise SaveFailed("internal", f"{type(error).__name__}: {error}") from error

    def _run(self) -> SaveResult:
        target = Path(os.path.realpath(self._target))
        self._refuse_unsuitable(target)
        mode = self._target_mode(target)
        fd, name = self._open_temp(target)
        renamed = False
        reader: int | None = None
        try:
            total = self._planner.length()
            self._preallocate(fd, total)
            scanner, entries, longs, written = self._stream(fd, total)
            if written != total:
                raise SaveFailed("internal", "the document changed during the save")
            self._report("flushing", written, total)
            self._set_mode(fd, mode)  # before the final fsync, so a crash after the rename cannot leave the mkstemp mode 0600
            self._sync(fd)
            info = os.fstat(fd)
            reader = self._io.open_ro(name)
            self._check_cancel()  # the last cancel point
            self._replace(name, target)
            renamed = True
            adopted, reader = reader, None  # the finish step owns the descriptor from here on
            with contextlib.suppress(OSError):
                self._io.fsync_dir(str(target.parent))
            return self._finish(adopted, info, target, scanner, entries, longs, total, mode)
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)
            if reader is not None:
                with contextlib.suppress(OSError):
                    os.close(reader)
            if renamed or self._remove_temp(name):
                _forget_temp(name)

    def _finish(
        self,
        reader: int,
        info: os.stat_result,
        target: Path,
        scanner: RowScanner | None,
        entries: list[int],
        longs: list[LongRow],
        total: int,
        mode: int,
    ) -> SaveResult:
        """Switch to the new file after the replace; any failure is reported as `committed` and releases the descriptor."""
        source: PreadSource | None = None
        try:
            self._report("finishing", total, total)
            source = PreadSource.from_fd(reader, identity=FileIdentity.from_stat(info))
            line_index = self._build_index(source, scanner, entries, longs, total)
        except Exception as error:
            if source is not None:
                source.close()
            else:
                with contextlib.suppress(OSError):
                    os.close(reader)
            detail = str(error) if isinstance(error, OSError) else f"{type(error).__name__}: {error}"
            message = f"the file was written but the editor could not switch to it: {detail}"
            raise SaveFailed("internal", OSError(error.errno, message) if isinstance(error, OSError) and error.errno else message, committed=True) from error
        return SaveResult(target, total, source, line_index, self._layout, mode)

    def _stream(self, fd: int, total: int) -> tuple[RowScanner | None, list[int], list[LongRow], int]:
        """Write the document chunk by chunk; return the scanner (or `None`), its entries and long rows, and the bytes written."""
        settings = self._settings
        scanner = RowScanner(settings.stride, settings.long_line_threshold) if settings.build_index else None
        entries: list[int] = []
        longs: list[LongRow] = []
        written = unsynced = 0
        while written < total:
            self._check_cancel()
            want = min(settings.chunk, total - written)
            parts = self._planner.plan(written, want, self._unverified)
            try:
                data = b"".join(part.source.read(part.a, part.b - part.a, cache=False) if part.src == 0 else part.source.read(part.a, part.b - part.a) for part in parts)
            except SourceChanged as error:
                if error.kind is ChangeKind.TRUNCATED:
                    raise SaveFailed("write", "the file was truncated; the unchanged parts cannot be read") from error
                raise SaveFailed("write", f"the file changed while saving: {error}") from error
            if len(data) != want:
                raise SaveFailed("write", "short read while saving")
            self._write_all(fd, data)
            out = written
            for part in parts:
                self._layout.add(part.src, part.a, part.b, out)
                out += part.b - part.a
            if scanner is not None:
                scanner.feed(data, written, final=written + len(data) >= total)
                new_entries, new_longs = scanner.take()
                entries += new_entries
                longs += new_longs
            written += len(data)
            unsynced += len(data)
            if unsynced >= settings.fsync_every:
                self._sync(fd)
                unsynced = 0
            self._report("writing", written, total)
            self._give_way()
        return scanner, entries, longs, written

    def _build_index(self, source: PreadSource, scanner: RowScanner | None, entries: list[int], longs: list[LongRow], total: int) -> LineIndex | None:
        if scanner is None:
            return None
        settings = self._settings
        return LineIndex.from_scan(
            source,
            scanner,
            entries,
            longs + scanner.finish(total),
            total,
            stride=settings.stride,
            long_line_threshold=settings.long_line_threshold,
            long_line_cap=settings.long_line_cap,
        )

    def _remove_temp(self, name: str) -> bool:
        """Unlink the temp file; return whether it is gone (otherwise the exit hook retries)."""
        try:
            self._io.unlink(name)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def _check_cancel(self) -> None:
        if self._cancelled.is_set():
            raise SaveCancelled

    def _refuse_unsuitable(self, target: Path) -> None:
        try:
            info = os.stat(target)
        except FileNotFoundError:
            return
        except OSError as error:
            raise SaveFailed("prepare", error) from error
        if not stat.S_ISREG(info.st_mode):
            raise SaveFailed("prepare", f"not a regular file: {target}")
        if not os.access(target, os.W_OK):
            raise SaveFailed("prepare", "read-only file")

    def _target_mode(self, target: Path) -> int:
        try:
            return stat.S_IMODE(os.stat(target).st_mode)
        except FileNotFoundError:
            return 0o666 & ~self._io.umask()
        except OSError as error:
            raise SaveFailed("prepare", error) from error

    def _open_temp(self, target: Path) -> tuple[int, str]:
        stem = os.fsencode(target.name)[:NAME_BYTES].decode(errors="ignore")
        try:
            fd, name = self._io.mkstemp(dir=target.parent, prefix=f".{stem}.", suffix=".tmp")
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EROFS}:
                message = f"cannot create a temporary file in {target.parent}; use Save As"
                raise SaveFailed("prepare", OSError(error.errno, message)) from error
            raise SaveFailed("prepare", error) from error
        with _TEMP_LOCK:
            _TEMP_NAMES.add(name)
        return fd, name

    def _preallocate(self, fd: int, total: int) -> None:
        if not self._settings.preallocate or total <= 0:
            return
        try:
            self._io.fallocate(fd, 0, total)
        except OSError as error:
            if error.errno not in UNSUPPORTED:
                raise SaveFailed("prepare", error) from error

    def _write_all(self, fd: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            try:
                count = self._io.write(fd, view)
            except OSError as error:
                raise SaveFailed("write", error) from error
            if count <= 0:
                raise SaveFailed("write", "the file accepted no bytes")
            view = view[count:]

    def _sync(self, fd: int) -> None:
        try:
            self._io.fsync(fd)
        except OSError as error:
            raise SaveFailed("flush", error) from error

    def _set_mode(self, fd: int, mode: int) -> None:
        try:
            self._io.fchmod(fd, mode)
        except OSError as error:
            if error.errno not in {errno.ENOTSUP, errno.EOPNOTSUPP}:
                raise SaveFailed("flush", error) from error

    def _replace(self, name: str, target: Path) -> None:
        try:
            self._io.replace(name, str(target))
        except OSError as error:
            raise SaveFailed("replace", error) from error

    def _report(self, phase: str, done: int, total: int) -> None:
        if self._progress is None:
            return
        if phase == "writing" and done < total:
            now = self._clock()
            if self._last_report is not None and now - self._last_report < PROGRESS_INTERVAL:
                return
            self._last_report = now
        self._progress(SaveProgress(phase, done, total))

    def _give_way(self) -> None:
        if self._foreground is not None:
            time.sleep(self._foreground.pause_seconds())
