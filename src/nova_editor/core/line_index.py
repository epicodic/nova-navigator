"""Sparse background line index over the original file (ADR-5, DEC-10, DEC-13, DEC-14).

Rows end at LF, CRLF or a lone CR. Terminator bytes belong to the row. A file ending in a terminator has a final empty row;
an empty file has exactly one empty row. Unicode separators, VT, FF and NEL are ordinary content.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from array import array
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType

from nova_editor.core.byte_source import ByteSource, SourceChanged

DEFAULT_STRIDE = 64
DEFAULT_LONG_LINE_THRESHOLD = 16 * 1024
DEFAULT_LONG_LINE_CAP = 524_288
DEFAULT_READ_BUDGET = 4 * 1024 * 1024
DEFAULT_MAX_LINES_PER_CALL = 128
DEFAULT_SCAN_BLOCK = 1 << 20
DEFAULT_WALK_WINDOW = 64 * 1024

_LOG = logging.getLogger(__name__)
_TERMINATOR = re.compile(rb"\r\n|\n|\r")
TAIL_BYTES = 2  # the longest terminator

# (row, start, end) of a row longer than the long-line threshold
_LongRow = tuple[int, int, int]


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


@dataclass
class _ScanState:
    count: int = 1
    prev: int = 0
    carry: bool = False
    entries: list[int] = field(default_factory=list)  # stride entries found in the current block
    longs: list[_LongRow] = field(default_factory=list)  # long rows found in the current block


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
            match = _TERMINATOR.search(self._buf, self.pos - self._base)
            buffer_end = self._base + len(self._buf)
            if match is not None:
                lone_cr_at_edge = match.end() == len(self._buf) and match.group() == b"\r"
                if not (lone_cr_at_edge and buffer_end < self._length):
                    self.pos = self._base + match.end()
                    return self.pos, len(match.group())
            elif buffer_end >= self._length:
                return None
            self._fill()


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
        self._lock = threading.Lock()
        self._starts = array("Q", [0])
        self._long_rows = array("Q")  # row numbers of recorded long rows, ascending
        self._long_starts = array("Q")
        self._long_ends = array("Q")
        self._long_overflow = False
        self._count = 1
        self._complete = False
        self._error: BaseException | None = None
        self._cancelled = threading.Event()
        self._subscribers: list[Callable[[], None]] = []
        self._thread: threading.Thread | None = None

    # -- public -------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("scan already started")
        self._thread = threading.Thread(target=self._run, name="line-index-scan", daemon=True)
        self._thread.start()

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
            return LineSnapshot(self._count, self._complete, self._error)

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
        state = _ScanState()
        pos = 0
        length = self._length
        while pos < length and not self._cancelled.is_set():
            block = self._source.read(pos, self._scan_block, cache=False)
            if not block:
                raise SourceChanged("unexpected empty read during the scan")
            self._scan_bytes(block, pos, pos + len(block) >= length, state)
            pos += len(block)
            self._publish(state.count, state.entries, state.longs, complete=False)
            state.entries, state.longs = [], []
            self._notify()
            time.sleep(self._yield_seconds)
        if pos >= length and not self._cancelled.is_set():
            last = [(state.count - 1, state.prev, length)] if length - state.prev > self._threshold else []
            self._publish(state.count, [], last, complete=True)

    def _publish(self, count: int, entries: list[int], longs: list[_LongRow], *, complete: bool) -> None:
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
            self._complete = complete

    def _notify(self) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            call_subscriber(callback)

    def _scan_bytes(self, block: bytes, base: int, final: bool, state: _ScanState) -> None:
        begin = 0
        if state.carry:
            state.carry = False
            begin = 1 if block.startswith(b"\n") else 0
            self._boundary(base + begin, state)
        if block.find(b"\r", begin) == -1:
            self._scan_lf(block, base, begin, state)
        else:
            self._scan_mixed(block, base, begin, final, state)

    def _boundary(self, nxt: int, state: _ScanState) -> None:
        """Register the start `nxt` of row `state.count`, ending the row that began at `state.prev`."""
        if nxt - state.prev > self._threshold:
            state.longs.append((state.count - 1, state.prev, nxt))
        if state.count % self._stride == 0:
            state.entries.append(nxt)
        state.count += 1
        state.prev = nxt

    def _scan_lf(self, block: bytes, base: int, begin: int, state: _ScanState) -> None:
        """Fast path for a block without CR: locals only, one threshold comparison per row."""
        stride, threshold = self._stride, self._threshold
        entries, longs = state.entries, state.longs
        count, prev = state.count, state.prev
        find = block.find
        i = find(b"\n", begin)
        while i != -1:
            nxt = base + i + 1
            if nxt - prev > threshold:
                longs.append((count - 1, prev, nxt))
            if count % stride == 0:
                entries.append(nxt)
            count += 1
            prev = nxt
            i = find(b"\n", i + 1)
        state.count, state.prev = count, prev

    def _scan_mixed(self, block: bytes, base: int, begin: int, final: bool, state: _ScanState) -> None:
        for match in _TERMINATOR.finditer(block, begin):
            if match.end() == len(block) and not final and match.group() == b"\r":
                state.carry = True  # a CR at the block edge may be the first half of a CRLF
                break
            self._boundary(base + match.end(), state)
