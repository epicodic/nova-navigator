"""Sparse background line index over the original file (ADR-5, DEC-10, DEC-13, DEC-14).

Rows end at LF, CRLF or a lone CR. Terminator bytes belong to the row. A file ending in a terminator has a final empty row;
an empty file has exactly one empty row. Unicode separators, VT, FF and NEL are ordinary content.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass

from nova_editor.core.byte_source import ByteSource, SourceChanged

DEFAULT_STRIDE = 64
DEFAULT_READ_BUDGET = 4 * 1024 * 1024
DEFAULT_MAX_LINES_PER_CALL = 128
DEFAULT_SCAN_BLOCK = 1 << 20
DEFAULT_WALK_WINDOW = 64 * 1024

_TERMINATOR = re.compile(rb"\r\n|\n|\r")


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


class _OverBudget(Exception):
    """A walk would exceed the per-call read budget."""


@dataclass
class _ScanState:
    count: int = 1
    prev: int = 0
    carry: bool = False


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

    Every `stride`-th row start is stored. UI-path calls (`row_range`, `lines`) walk forward from the nearest entry and read at
    most `read_budget` bytes; they never wait for the scan.
    """

    def __init__(
        self,
        source: ByteSource,
        *,
        stride: int = DEFAULT_STRIDE,
        read_budget: int = DEFAULT_READ_BUDGET,
        max_lines_per_call: int = DEFAULT_MAX_LINES_PER_CALL,
        scan_block: int = DEFAULT_SCAN_BLOCK,
        walk_window: int = DEFAULT_WALK_WINDOW,
        yield_seconds: float = 0.0,
    ) -> None:
        if min(stride, read_budget, max_lines_per_call, scan_block, walk_window) <= 0:
            raise ValueError("stride, read_budget, max_lines_per_call, scan_block and walk_window must be positive")
        self._source = source
        self._length = source.length()
        self._stride = stride
        self._read_budget = read_budget
        self._max_lines = max_lines_per_call
        self._scan_block = scan_block
        self._walk_window = walk_window
        self._yield_seconds = yield_seconds
        self._lock = threading.Lock()
        self._starts = array("Q", [0])
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
        """Register a callback run on the scan thread, outside every lock, after progress and at completion."""
        with self._lock:
            self._subscribers.append(callback)

    def snapshot(self) -> LineSnapshot:
        with self._lock:
            return LineSnapshot(self._count, self._complete, self._error)

    def stored_entries(self) -> int:
        with self._lock:
            return len(self._starts)

    def row_range(self, row: int) -> RowRange | None:
        """Return the byte range of `row`, or `None` when it is not known yet or cannot be resolved within the read budget."""
        if row < 0:
            raise IndexError(row)
        found = self._resolve(row, 1)
        return found[0] if found else None

    def lines(self, first: int, count: int) -> list[RowRange]:
        """Return up to `min(count, max_lines_per_call)` consecutive known rows from `first` (a prefix when the frontier or the budget is reached)."""
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
        walker = _Walker(self._source, self._length, pos, self._read_budget, self._walk_window)
        out: list[RowRange] = []
        try:
            while row < stop:
                start = walker.pos
                found = walker.advance()
                end, terminator = (self._length, 0) if found is None else found
                if row >= first:
                    out.append(RowRange(start, end - terminator, end))
                row += 1
        except _OverBudget:
            pass
        return out

    # -- scan thread --------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            self._scan()
        except SourceChanged as error:
            self._record_error(error)
        except Exception as error:
            self._record_error(error)
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
            entries: list[int] = []
            self._scan_bytes(block, pos, pos + len(block) >= length, state, entries)
            pos += len(block)
            self._publish(state.count, entries, complete=False)
            self._notify()
            time.sleep(self._yield_seconds)
        if pos >= length and not self._cancelled.is_set():
            self._publish(state.count, [], complete=True)

    def _publish(self, count: int, entries: list[int], *, complete: bool) -> None:
        with self._lock:
            self._starts.extend(entries)
            self._count = count
            self._complete = complete

    def _notify(self) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            with contextlib.suppress(Exception):
                callback()

    def _scan_bytes(self, block: bytes, base: int, final: bool, state: _ScanState, entries: list[int]) -> None:
        count, prev = state.count, state.prev
        begin = 0
        if state.carry:
            state.carry = False
            begin = 1 if block.startswith(b"\n") else 0
            count, prev = self._boundary(base + begin, count, entries)
        if block.find(b"\r", begin) == -1:
            count, prev = self._scan_lf(block, base, begin, count, prev, entries)
        else:
            count, prev = self._scan_mixed(block, base, begin, final, state, count, prev, entries)
        state.count, state.prev = count, prev

    def _boundary(self, nxt: int, count: int, entries: list[int]) -> tuple[int, int]:
        """Register the start `nxt` of row `count`; return the new `(count, prev)`."""
        if count % self._stride == 0:
            entries.append(nxt)
        return count + 1, nxt

    def _scan_lf(self, block: bytes, base: int, begin: int, count: int, prev: int, entries: list[int]) -> tuple[int, int]:
        stride = self._stride
        find = block.find
        i = find(b"\n", begin)
        while i != -1:
            prev = base + i + 1
            if count % stride == 0:
                entries.append(prev)
            count += 1
            i = find(b"\n", i + 1)
        return count, prev

    def _scan_mixed(self, block: bytes, base: int, begin: int, final: bool, state: _ScanState, count: int, prev: int, entries: list[int]) -> tuple[int, int]:
        for match in _TERMINATOR.finditer(block, begin):
            if match.end() == len(block) and not final and match.group() == b"\r":
                state.carry = True  # a CR at the block edge may be the first half of a CRLF
                break
            prev = base + match.end()
            count, _ = self._boundary(prev, count, entries)
        return count, prev
