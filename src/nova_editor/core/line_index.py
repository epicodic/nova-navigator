"""Sparse background line index over the original file (ADR-5, DEC-10, DEC-13, DEC-14).

Rows end at LF, CRLF or a lone CR. Terminator bytes belong to the row. A file ending in a terminator has a final empty row;
an empty file has exactly one empty row. Unicode separators, VT, FF and NEL are ordinary content.
"""

from __future__ import annotations

import logging
import threading
import time
from array import array
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import TypedDict, Unpack

from nova_editor.core.byte_source import ByteSource, SourceChanged
from nova_editor.core.foreground import Foreground
from nova_editor.core.row_scanner import TERMINATOR, LongRow, RowScanner

DEFAULT_STRIDE = 64
DEFAULT_LONG_LINE_THRESHOLD = 16 * 1024
DEFAULT_LONG_LINE_CAP = 524_288
DEFAULT_READ_BUDGET = 4 * 1024 * 1024
DEFAULT_MAX_LINES_PER_CALL = 128
DEFAULT_SCAN_BLOCK = 1 << 20
DEFAULT_WALK_WINDOW = 64 * 1024

_LOG = logging.getLogger(__name__)
TAIL_BYTES = 2  # the longest terminator


@dataclass(frozen=True)
class RowRange:
    """Absolute byte range of one row: `start <= content_end <= end`; the bytes from `content_end` to `end` are the terminator."""

    start: int
    content_end: int
    end: int

    @property
    def terminator_length(self) -> int:
        return self.end - self.content_end


@dataclass(frozen=True)
class LineSnapshot:
    """Consistent view of the scan: `count` is a lower bound until `complete` (REQ-6)."""

    count: int
    complete: bool
    error: BaseException | None
    scanned_bytes: int = 0  # bytes covered by the scan so far (start of the still-open last row); equals the length once complete


class _ContainAndLog:
    """Context manager that swallows an `Exception` from its block and logs it at debug level."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> bool:
        if exc is None or not isinstance(exc, Exception):
            return False
        _LOG.debug("subscriber callback raised", exc_info=exc)
        return True


def call_subscriber(callback: Callable[[], None]) -> None:
    """Run a subscriber callback; an exception it raises is contained and logged at debug level."""
    with _ContainAndLog():
        callback()


class _OverBudget(Exception):
    """A walk would exceed the per-call read budget."""


class _Walker:
    """Finds row ends sequentially through a byte window and counts the bytes it asks the source for."""

    def __init__(self, source: ByteSource, length: int, pos: int, budget: int, window: int) -> None:
        self._source = source
        self._length = length
        self._budget = budget
        self._window = window
        self.pos = pos
        self.spent = 0
        self._buf = b""
        self._base = pos

    def charge(self, size: int) -> None:
        self.spent += size
        if self.spent > self._budget:
            raise _OverBudget

    def jump(self, pos: int) -> None:
        self.pos = pos
        self._buf = b""
        self._base = pos

    def _fill(self) -> None:
        cut = self.pos - self._base
        offset = self._base + len(self._buf)
        want = min(self._window, self._length - offset)
        self.charge(want)
        data = self._source.read(offset, want)
        if not data:
            raise SourceChanged("unexpected empty read during a walk")
        self._buf = self._buf[cut:] + data
        self._base += cut

    def advance(self) -> tuple[int, int] | None:
        """Return `(end, terminator_length)` of the row starting at `pos` and move to its end; `None` when it is the last row."""
        while True:
            match = TERMINATOR.search(self._buf, self.pos - self._base)
            buffer_end = self._base + len(self._buf)
            if match is not None:
                lone_cr_at_edge = match.end() == len(self._buf) and match.group() == b"\r"
                if not (lone_cr_at_edge and buffer_end < self._length):
                    self.pos = self._base + match.end()
                    return self.pos, len(match.group())
            elif buffer_end >= self._length:
                return None
            self._fill()


class LineIndexSettings(TypedDict, total=False):
    """Keyword settings of `LineIndex`."""

    stride: int
    long_line_threshold: int
    long_line_cap: int
    read_budget: int
    max_lines_per_call: int
    scan_block: int
    walk_window: int
    yield_seconds: float
    foreground: Foreground | None


class LineIndex:
    """Sparse row index built by a background thread that only appends.

    Every `stride`-th row start is stored. Rows longer than `long_line_threshold` are recorded in a side table (at most
    `long_line_cap` entries) so that walks cross them without reading. UI-path calls (`row_range`, `lines`) walk forward from
    the nearest entry and read at most `read_budget` bytes; they never wait for the scan.
    """

    def __init__(
        self,
        source: ByteSource,
        *,
        stride: int = DEFAULT_STRIDE,
        long_line_threshold: int = DEFAULT_LONG_LINE_THRESHOLD,
        long_line_cap: int = DEFAULT_LONG_LINE_CAP,
        read_budget: int = DEFAULT_READ_BUDGET,
        max_lines_per_call: int = DEFAULT_MAX_LINES_PER_CALL,
        scan_block: int = DEFAULT_SCAN_BLOCK,
        walk_window: int = DEFAULT_WALK_WINDOW,
        yield_seconds: float = 0.0,
        foreground: Foreground | None = None,
    ) -> None:
        if min(stride, long_line_threshold, read_budget, max_lines_per_call, scan_block, walk_window) <= 0:
            raise ValueError("stride, long_line_threshold, read_budget, max_lines_per_call, scan_block and walk_window must be positive")
        if long_line_cap < 0:
            raise ValueError("long_line_cap must not be negative")
        self._source = source
        self._length = source.length()
        self._stride = stride
        self._threshold = long_line_threshold
        self._long_cap = long_line_cap
        self._read_budget = read_budget
        self._max_lines = max_lines_per_call
        self._scan_block = scan_block
        # An unrecorded row is at most `long_line_threshold` bytes plus its terminator, so one fill of that size always finds its
        # end. Larger fills would over-read at every jump over a recorded row and break the byte bound (design 5.4).
        self._fill_size = min(walk_window, long_line_threshold + TAIL_BYTES)
        self._yield_seconds = yield_seconds
        self._foreground = foreground
        self._lock = threading.Lock()
        self._starts = array("Q", [0])
        self._long_rows = array("Q")  # row numbers of recorded long rows, ascending
        self._long_starts = array("Q")
        self._long_ends = array("Q")
        self._long_overflow = False
        self._count = 1
        self._scanned = 0
        self._complete = False
        self._error: BaseException | None = None
        self._cancelled = threading.Event()
        self._subscribers: list[Callable[[], None]] = []
        self._thread: threading.Thread | None = None
        self._inline = False

    @classmethod
    def from_scan(cls, source: ByteSource, scanner: RowScanner, entries: list[int], longs: list[LongRow], length: int, **settings: Unpack[LineIndexSettings]) -> LineIndex:
        """Create a complete index over `source` from a scanner that saw every byte of it.

        `entries` and `longs` are the concatenation of every `scanner.take()` result plus `scanner.finish(length)`.
        The index is complete at once and refuses `start()` and `scan_now()`.
        """
        index = cls(source, **settings)
        index._inline = True
        index._publish(scanner.count, entries, longs, length, complete=True)
        return index

    # -- public -------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None or self._inline:
            raise RuntimeError("scan already started")
        self._thread = threading.Thread(target=self._run, name="line-index-scan", daemon=True)
        self._thread.start()

    def scan_now(self) -> None:
        """Run the whole scan inline on the calling thread; the results equal those of the threaded scan.

        Meant for small sources (the document layer uses it up to 1 MiB).
        Subscribers are notified as after a threaded scan.
        A `SourceChanged` is recorded in the snapshot as for the thread; any other exception is recorded and raised.

        Raises:
            RuntimeError: when the scan was already started, threaded or inline.
        """
        if self._thread is not None or self._inline:
            raise RuntimeError("scan already started")
        self._inline = True
        self._run()

    def cancel(self) -> None:
        self._cancelled.set()

    def join(self, timeout: float | None = None) -> bool:
        """Wait for the scan thread (tests and shutdown only); return whether the scan completed."""
        if self._thread is not None:
            self._thread.join(timeout)
        return self.snapshot().complete

    def subscribe(self, callback: Callable[[], None]) -> None:
        """Register a callback run on the scan thread, outside every lock, after progress and at completion.

        When the scan already completed or failed, the callback is also run once, immediately, on the calling thread (outside
        the lock), so a late subscriber sees the terminal state.
        An exception raised by a callback is contained and logged at debug level.
        """
        with self._lock:
            self._subscribers.append(callback)
            terminal = self._complete or self._error is not None
        if terminal:
            call_subscriber(callback)

    def snapshot(self) -> LineSnapshot:
        with self._lock:
            return LineSnapshot(self._count, self._complete, self._error, self._scanned)

    def stored_entries(self) -> int:
        with self._lock:
            return len(self._starts)

    @property
    def long_row_count(self) -> int:
        """Number of recorded rows longer than the long-line threshold."""
        with self._lock:
            return len(self._long_rows)

    @property
    def long_overflow(self) -> bool:
        """Whether long rows beyond the side-table cap were left unrecorded.

        Once set, walks over an unrecorded long row can exceed the read budget, so `row_range` and `lines` may return `None`
        or a short list for rows the scan has already counted.
        """
        with self._lock:
            return self._long_overflow

    def row_range(self, row: int) -> RowRange | None:
        """Return the byte range of `row`, or `None` when it is not known yet or cannot be resolved within the read budget.

        `None` therefore means one of two things: the scan has not reached the row yet, or the walk from the nearest stored
        entry would exceed `read_budget` (for example a long row that overflowed the side table).
        To tell them apart, check `snapshot().complete` and `long_overflow`: on a complete scan every row exists, so `None`
        means the budget was exceeded; on an incomplete scan a row at or beyond `snapshot().count - 1` is not scanned yet,
        and a row below it that returns `None` was cut off by the budget, most likely because `long_overflow` is set.

        Raises `SourceChanged` when the walk's own source reads find that the file changed.
        """
        if row < 0:
            raise IndexError(row)
        found = self._resolve(row, 1)
        return found[0] if found else None

    def row_at_offset(self, offset: int) -> tuple[int, RowRange] | None:
        """Return `(row, range)` of the row containing byte `offset`; terminator bytes belong to their row.

        `offset == length` maps to the last row once the scan is complete.
        Returns `None` when the offset is negative, beyond the length, not indexed yet (`snapshot().scanned_bytes`), or when the
        walk from the nearest stored entry would exceed `read_budget`.

        Raises `SourceChanged` when the walk's own source reads find that the file changed.
        """
        with self._lock:
            complete, count, scanned = self._complete, self._count, self._scanned
            if offset < 0 or offset > self._length:
                return None
            if offset == self._length and complete:
                at_end = True
            elif offset >= scanned:
                return None
            else:
                at_end = False
                entry = bisect_right(self._starts, offset) - 1
                row = entry * self._stride
                pos = self._starts[entry]
                lo = bisect_left(self._long_starts, pos)
                hi = bisect_right(self._long_starts, offset)
                recorded = {self._long_rows[i]: (self._long_starts[i], self._long_ends[i]) for i in range(lo, hi)}
        if at_end:
            found = self.row_range(count - 1)
            return None if found is None else (count - 1, found)
        walker = _Walker(self._source, self._length, pos, self._read_budget, self._fill_size)
        try:
            while True:
                long_row = recorded.get(row)
                if long_row is not None:
                    start, end = long_row
                    walker.jump(end)
                    terminator = self._tail_terminator(walker, start, end) if end > offset else 0
                else:
                    start = walker.pos
                    found_end = walker.advance()
                    end, terminator = (self._length, 0) if found_end is None else found_end
                if end > offset:
                    return row, RowRange(start, end - terminator, end)
                row += 1
        except _OverBudget:
            return None

    def lines(self, first: int, count: int) -> list[RowRange]:
        """Return up to `min(count, max_lines_per_call)` consecutive known rows from `first`.

        A list shorter than requested is a prefix: it ends where the scan frontier is (the rows are not scanned yet) or where
        the read budget is exhausted (the next row is unresolved, for example after a side-table overflow).
        To tell them apart, check `snapshot().complete` and `long_overflow`.

        Raises `SourceChanged` when the walk's own source reads find that the file changed.
        """
        if first < 0:
            raise IndexError(first)
        if count <= 0:
            return []
        return self._resolve(first, min(count, self._max_lines))

    # -- UI-path walk -------------------------------------------------------------------------
    def _resolve(self, first: int, limit: int) -> list[RowRange]:
        with self._lock:
            resolvable = self._count if self._complete else self._count - 1
            stop = min(resolvable, first + limit)
            if first >= stop:
                return []
            entry = first // self._stride
            row = entry * self._stride
            pos = self._starts[entry]
            recorded = self._recorded_between(row, stop)
        walker = _Walker(self._source, self._length, pos, self._read_budget, self._fill_size)
        out: list[RowRange] = []
        try:
            while row < stop:
                long_row = recorded.get(row)
                if long_row is not None:
                    start, end = long_row
                    walker.jump(end)
                    terminator = self._tail_terminator(walker, start, end) if row >= first else 0
                else:
                    start = walker.pos
                    found = walker.advance()
                    end, terminator = (self._length, 0) if found is None else found
                if row >= first:
                    out.append(RowRange(start, end - terminator, end))
                row += 1
        except _OverBudget:
            pass
        return out

    def _recorded_between(self, low: int, high: int) -> dict[int, tuple[int, int]]:
        """Return `{row: (start, end)}` of the recorded long rows in `[low, high)`; the caller holds the lock."""
        lo = bisect_left(self._long_rows, low)
        hi = bisect_left(self._long_rows, high)
        return {self._long_rows[i]: (self._long_starts[i], self._long_ends[i]) for i in range(lo, hi)}

    def _tail_terminator(self, walker: _Walker, start: int, end: int) -> int:
        """Return the terminator length of the row `[start, end)` from a tail read charged to the walker."""
        size = min(TAIL_BYTES, end - start)
        if size == 0:
            return 0
        walker.charge(size)
        tail = self._source.read(end - size, size)
        if len(tail) != size:
            raise SourceChanged("short read of a long row tail")
        return 2 if tail.endswith(b"\r\n") else 1 if tail.endswith((b"\n", b"\r")) else 0

    # -- scan thread --------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            self._scan()
        except SourceChanged as error:
            self._record_error(error)
        except Exception as error:
            self._record_error(error)
            self._notify()
            raise
        self._notify()

    def _record_error(self, error: BaseException) -> None:
        with self._lock:
            self._error = error

    def _scan(self) -> None:
        scanner = RowScanner(self._stride, self._threshold)
        pos = 0
        length = self._length
        while pos < length and not self._cancelled.is_set():
            block = self._source.read(pos, self._scan_block, cache=False)
            if not block:
                raise SourceChanged("unexpected empty read during the scan")
            scanner.feed(block, pos, final=pos + len(block) >= length)
            pos += len(block)
            entries, longs = scanner.take()
            self._publish(scanner.count, entries, longs, scanner.prev, complete=False)
            self._notify()
            time.sleep(self._yield_seconds)
            pause = 0.0 if self._foreground is None else self._foreground.pause_seconds()
            if pause > 0:
                time.sleep(pause)  # the UI is busy: hand it the GIL (see `Foreground`)
        if pos >= length and not self._cancelled.is_set():
            self._publish(scanner.count, [], scanner.finish(length), length, complete=True)

    def _publish(self, count: int, entries: list[int], longs: list[LongRow], scanned: int, *, complete: bool) -> None:
        with self._lock:
            self._starts.extend(entries)
            room = max(self._long_cap - len(self._long_rows), 0)
            for row, start, end in longs[:room]:
                self._long_rows.append(row)
                self._long_starts.append(start)
                self._long_ends.append(end)
            if len(longs) > room:
                self._long_overflow = True
            self._count = count
            self._scanned = scanned
            self._complete = complete

    def _notify(self) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            call_subscriber(callback)
