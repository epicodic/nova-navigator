"""The in-place save job: patch the changed ranges of a document into the existing file with `pwrite` (ACT3 design 3, ADR-3 rev 2).

The job knows the document only through `InPlaceHost`.
Order: plan, `refuse_unsuitable`, `check_path`, open `O_WRONLY` without truncation and re-verify the identity on the descriptor, copy the old
bytes of the ranges to write, install the read overlay, `pwrite` the ranges, `fsync`, adopt the identity.
Before the first byte is written a failure or a cancel changes nothing; after it a failure is reported as partial and the edits are kept.
"""

from __future__ import annotations

import contextlib
import errno
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from nova_editor.core.byte_source import ChangeKind, FileIdentity, SourceChanged
from nova_editor.core.redirect import Copy
from nova_editor.core.save import (
    PROGRESS_INTERVAL,
    PlanPart,
    SaveCancelled,
    SaveFailed,
    SaveIo,
    SaveProgress,
    SaveSettings,
    check_path,
    refuse_unsuitable,
)

__all__ = ["InPlaceHost", "InPlaceJob", "InPlacePlan", "InPlaceResult"]


@dataclass(frozen=True)
class InPlacePlan:
    """The outcome of `LazyDocument.plan_in_place`: the document length and the ascending, merged document ranges to write."""

    length: int
    ranges: tuple[tuple[int, int], ...]


class InPlaceHost(Protocol):
    """What the job needs from the document."""

    def plan_in_place(self) -> InPlacePlan | None: ...

    def plan(self, offset: int, limit: int, unverified: bool) -> list[PlanPart]: ...

    def prepare_patch(self, ranges: Sequence[tuple[int, int]], chunk: int, check: Callable[[], None]) -> list[Copy]: ...

    def begin_patch(self, copies: Sequence[Copy], ranges: Sequence[tuple[int, int]]) -> None: ...

    def end_patch(self, identity: FileIdentity | None) -> None: ...


@dataclass(frozen=True)
class InPlaceResult:
    """The outcome of a successful in-place save."""

    target: Path
    length: int
    identity: FileIdentity
    written: int
    copies: tuple[Copy, ...]


class InPlaceJob:
    """Patch the ranges of the document that differ from the file into the file itself.

    `run()` has exactly one outcome: an `InPlaceResult`, `SaveCancelled` (before the first byte was written) or `SaveFailed`.
    A failure before the first byte leaves the document, the file and the patched ranges as they were (`SaveFailed.partial` is false); one after it
    is partial: the file may hold some of the new bytes, the read overlay and the patched ranges stay, and the caller keeps the edits.
    """

    def __init__(
        self,
        host: InPlaceHost,
        target: Path | str,
        held: FileIdentity | None,
        settings: SaveSettings,
        progress: Callable[[SaveProgress], None] | None,
        io: SaveIo | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the job.

        Args:
            host: The document: supplies the plan, the parts of each chunk and the patch bookkeeping.
            target: The file to patch; symlinks are resolved.
            held: The identity of the file that the editor holds; any other state of the file blocks the write.
            settings: Chunk size.
            progress: Called from the job's thread with each report, or `None`.
            io: The file operations; `None` uses `os`.
            clock: Time source of the progress throttle.
        """
        self._host = host
        self._target = Path(target)
        self._held = held
        self._settings = settings
        self._progress = progress
        self._io = io if io is not None else SaveIo()
        self._clock = clock
        self._cancelled = threading.Event()
        self._started = threading.Event()
        self._last_report: float | None = None
        self._copies: list[Copy] = []
        self._window_identity: FileIdentity | None = None

    def cancel(self) -> None:
        """Ask the job to stop; it raises `SaveCancelled` at the next check unless the first byte was written already."""
        self._cancelled.set()

    @property
    def started(self) -> bool:
        """Whether the first byte was written (readable from any thread): from then on a cancel no longer takes effect."""
        return self._started.is_set()

    def run(self) -> InPlaceResult:
        """Run the save on the calling thread."""
        try:
            return self._run()
        except SaveCancelled:
            raise
        except SaveFailed as error:
            self._attach_identity(error)
            raise
        except OSError as error:
            raise self._attach_identity(SaveFailed("internal", error, partial=self.started)) from error
        except Exception as error:
            raise self._attach_identity(SaveFailed("internal", f"{type(error).__name__}: {error}", partial=self.started)) from error

    def _attach_identity(self, error: SaveFailed) -> SaveFailed:
        """Set the identity the file had when the patch window closed on a partial failure; return `error`."""
        if error.partial and error.identity is None:
            error.identity = self._window_identity
        return error

    def _run(self) -> InPlaceResult:
        plan = self._host.plan_in_place()
        if plan is None:
            raise SaveFailed("internal", "the document is not in an in-place layout")
        target = Path(os.path.realpath(self._target))
        refuse_unsuitable(target)
        held = self._held
        kind = check_path(target, held)
        if kind is not ChangeKind.UNCHANGED or held is None:
            raise self._changed(kind if held is not None else ChangeKind.CREATED)
        fd = self._open(target)
        try:
            self._check_identity(FileIdentity.from_stat(self._io.fstat(fd)), held)
            total = sum(end - start for start, end in plan.ranges)
            self._report("writing", 0, total)
            identity = self._patch(fd, plan, held, total)
            self._report("finishing", total, total)
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)
        return InPlaceResult(target, plan.length, identity, total, tuple(self._copies))

    def _patch(self, fd: int, plan: InPlacePlan, held: FileIdentity, total: int) -> FileIdentity:
        """Copy, protect, write and flush; return the identity of the file afterwards."""
        if not plan.ranges:
            self._check_cancel()
            self._sync(fd)
            return self._identity_of(fd)
        try:
            self._copies = self._host.prepare_patch(plan.ranges, self._settings.chunk, self._check_cancel)
        except SourceChanged as error:
            raise SaveFailed("prepare", f"the file changed while saving: {error}", change=error.kind) from error
        except OSError as error:
            raise SaveFailed("prepare", error) from error
        self._check_cancel()  # the last cancel point: the next call protects the old bytes and the one after writes
        self._host.begin_patch(self._copies, plan.ranges)
        open_window = True
        try:
            self._write(fd, plan.ranges, total)
            self._report("flushing", total, total)
            self._sync(fd)
            identity = self._identity_of(fd)
            if identity.size != held.size:
                raise SaveFailed("flush", f"the file size changed while saving: {held.size} -> {identity.size}", partial=self.started)
            open_window = False
            self._window_identity = identity
            try:
                self._host.end_patch(identity)
            except SourceChanged as error:
                raise SaveFailed("flush", f"the file changed while saving: {error}", partial=self.started, change=error.kind) from error
            return identity
        finally:
            if open_window:
                self._close_window(fd, held)

    def _close_window(self, fd: int, held: FileIdentity) -> None:
        """Close the patch window after a failure: roll back when no byte was written, otherwise adopt what the file looks like now."""
        if not self.started:
            with contextlib.suppress(Exception):
                self._host.end_patch(None)
            return
        with contextlib.suppress(Exception):  # the failure being reported is the one that matters
            try:
                current = FileIdentity.from_stat(self._io.fstat(fd))
            except OSError:
                current = held
            self._window_identity = current
            self._host.end_patch(current)

    def _write(self, fd: int, ranges: Sequence[tuple[int, int]], total: int) -> None:
        chunk = self._settings.chunk
        written = 0
        for start, end in ranges:
            offset = start
            while offset < end:
                size = min(chunk, end - offset)
                data = self._read(offset, size)
                self._pwrite(fd, data, offset)
                offset += size
                written += size
                self._report("writing", written, total)

    def _read(self, offset: int, size: int) -> bytes:
        """Return the document bytes `[offset, offset + size)`; an original part is legal only where an earlier save patched (design 3 step 7, DEC-17)."""
        parts = self._host.plan(offset, size, False)
        copied = [(copy.a, copy.b) for copy in self._copies]
        pieces: list[bytes] = []
        try:
            for part in parts:
                if part.src == 0:
                    if any(part.a < b and a < part.b for a, b in copied):
                        raise SaveFailed("internal", f"an original part [{part.a}, {part.b}) lies in a range that was never patched", partial=self.started)
                    pieces.append(part.source.read(part.a, part.b - part.a, cache=False))
                else:
                    pieces.append(part.source.read(part.a, part.b - part.a))
        except SourceChanged as error:
            raise SaveFailed("write", f"the file changed while saving: {error}", partial=self.started, change=error.kind) from error
        data = b"".join(pieces)
        if len(data) != size:
            raise SaveFailed("write", "short read while saving", partial=self.started)
        return data

    def _pwrite(self, fd: int, data: bytes, offset: int) -> None:
        view = memoryview(data)
        while view:
            try:
                count = self._io.pwrite(fd, view, offset)
            except OSError as error:
                raise SaveFailed("write", error, partial=self.started) from error
            if count <= 0:
                raise SaveFailed("write", "the file accepted no bytes", partial=self.started)
            self._started.set()
            view = view[count:]
            offset += count

    def _open(self, target: Path) -> int:
        try:
            return self._io.open_write(str(target))
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
                raise SaveFailed("prepare", "read-only file") from error
            raise SaveFailed("prepare", error) from error

    def _check_identity(self, found: FileIdentity, held: FileIdentity) -> None:
        """Compare the identity of the open descriptor with the held one: closes the gap between `check_path` and the open."""
        if found == held:
            return
        if (found.dev, found.ino) != (held.dev, held.ino):
            raise self._changed(ChangeKind.REPLACED)
        raise self._changed(ChangeKind.TRUNCATED if found.size < held.size else ChangeKind.MODIFIED)

    @staticmethod
    def _changed(kind: ChangeKind) -> SaveFailed:
        return SaveFailed("prepare", f"the file changed on disk ({kind.value})", change=kind)

    def _identity_of(self, fd: int) -> FileIdentity:
        try:
            return FileIdentity.from_stat(self._io.fstat(fd))
        except OSError as error:
            raise SaveFailed("flush", error, partial=self.started) from error

    def _sync(self, fd: int) -> None:
        try:
            self._io.fsync(fd)
        except OSError as error:
            raise SaveFailed("flush", error, partial=self.started) from error

    def _check_cancel(self) -> None:
        if self._cancelled.is_set() and not self.started:
            raise SaveCancelled

    def _report(self, phase: str, done: int, total: int) -> None:
        if self._progress is None:
            return
        if phase == "writing" and 0 < done < total:
            now = self._clock()
            if self._last_report is not None and now - self._last_report < PROGRESS_INTERVAL:
                return
            self._last_report = now
        self._progress(SaveProgress(phase, done, total, self.started))
