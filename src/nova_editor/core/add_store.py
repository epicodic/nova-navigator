"""Append-only store of the bytes added during editing, with a sparse row index per segment (ACT4 design 4.4).

Segments are numbered from 1 (piece source `0` is the original file).
Small appends extend the tail segment, a `bytearray` of at most `tail_limit` bytes; then the tail is sealed into immutable `bytes` and a new tail starts.
An append of `tail_limit` bytes or more becomes its own sealed segment.
Every segment keeps the start of every `stride`-th row, so row queries walk at most `stride` rows.
Rows follow the break rule of `pieces`: LF, CRLF or lone CR end a row.
A CR at the end of the tail followed by an LF in the next append is one CRLF, so the append that continues the tail rewrites that break.
"""

from __future__ import annotations

import re
import threading
from bisect import bisect_right

from nova_editor.core.pieces import CR

DEFAULT_TAIL_LIMIT = 64 * 1024
DEFAULT_ROW_STRIDE = 32

_BREAK = re.compile(rb"\r\n|\n|\r")


class _Segment:
    """Bytes of one segment with the break count and the sparse row index; guarded by the lock of the store."""

    def __init__(self, data: bytes | bytearray, stride: int) -> None:
        self.data: bytes | bytearray = data
        self.stride = stride
        self.breaks = 0
        self.entries: list[int] = []  # entries[k - 1] is the start offset of row k * stride
        self._scan(0)

    def _scan(self, start: int) -> None:
        for match in _BREAK.finditer(self.data, start):
            self.breaks += 1
            if self.breaks % self.stride == 0:
                self.entries.append(match.end())

    def extend(self, data: bytes) -> int:
        """Append `data` and return the offset where it starts."""
        old = len(self.data)
        start = old
        if not isinstance(self.data, bytearray):
            raise ValueError("segment is sealed")
        if old and data[:1] == b"\n" and self.data[old - 1] == CR:
            # The trailing CR counted as a break on its own; with the LF it is one CRLF.
            if self.breaks % self.stride == 0:
                self.entries.pop()
            self.breaks -= 1
            start = old - 1
        self.data.extend(data)
        self._scan(start)
        return old

    def row_of(self, offset: int) -> int:
        offset = min(max(offset, 0), len(self.data))
        slot = bisect_right(self.entries, offset)
        row = slot * self.stride
        pos = self.entries[slot - 1] if slot else 0
        for match in _BREAK.finditer(self.data, pos):
            if match.end() > offset:
                break
            row += 1
        return row

    def row_start(self, row: int) -> int | None:
        if row < 0 or row > self.breaks:
            return None
        slot = row // self.stride
        pos = self.entries[slot - 1] if slot else 0
        left = row - slot * self.stride
        if left == 0:
            return pos
        for match in _BREAK.finditer(self.data, pos):
            left -= 1
            if left == 0:
                return match.end()
        return None

    def seal(self) -> None:
        """Freeze the bytes into an immutable `bytes`."""
        self.data = bytes(self.data)


class AddSegment:
    """`PieceSource` view of one add segment; the tree uses it for pieces whose `src` names the segment."""

    def __init__(self, store: AddStore, segment: int) -> None:
        self._store = store
        self._segment = segment

    def row_of(self, offset: int) -> int:
        return self._store.row_of(self._segment, offset)

    def row_start(self, row: int) -> int | None:
        return self._store.row_start(self._segment, row)

    def breaks_between(self, a: int, b: int) -> int:
        return self._store.breaks_between(self._segment, a, b)

    def read(self, a: int, b: int) -> bytes:
        return self._store.read(self._segment, a, b)


class AddStore:
    """Append-only byte store in segments; all methods take one lock and are safe to call from several threads."""

    def __init__(self, *, tail_limit: int = DEFAULT_TAIL_LIMIT, stride: int = DEFAULT_ROW_STRIDE) -> None:
        if tail_limit <= 0 or stride <= 0:
            raise ValueError("tail_limit and stride must be positive")
        self._tail_limit = tail_limit
        self._stride = stride
        self._lock = threading.Lock()
        self._segments: list[_Segment] = []  # segment k is _segments[k - 1]
        self._tail: int = 0  # number of the open tail segment, 0 when there is none

    @property
    def segment_count(self) -> int:
        """Number of segments; valid segment numbers are `1..segment_count`."""
        with self._lock:
            return len(self._segments)

    def segment(self, segment: int) -> AddSegment:
        """Return the `PieceSource` view of `segment`."""
        with self._lock:
            self._get(segment)
        return AddSegment(self, segment)

    def length(self, segment: int) -> int:
        """Return the current number of bytes of `segment`."""
        with self._lock:
            return len(self._get(segment).data)

    def append(self, data: bytes) -> tuple[int, int, int]:
        """Append `data` and return `(segment, a, b)` with `b - a == len(data)`.

        An append that continues the tail returns the tail segment with `a` equal to the previous end, so the caller can extend the previous piece.

        Raises:
            ValueError: when `data` is empty.
        """
        if not data:
            raise ValueError("cannot append empty data")
        data = bytes(data)
        if len(data) >= self._tail_limit:
            own = _Segment(data, self._stride)  # indexed before the lock is taken
            with self._lock:
                self._segments.append(own)
                return len(self._segments), 0, len(data)
        with self._lock:
            if self._tail:
                tail = self._segments[self._tail - 1]
                if len(tail.data) + len(data) <= self._tail_limit:
                    start = tail.extend(data)
                    return self._tail, start, start + len(data)
                tail.seal()
            self._segments.append(_Segment(bytearray(data), self._stride))
            self._tail = len(self._segments)
            return self._tail, 0, len(data)

    def read(self, segment: int, a: int, b: int) -> bytes:
        """Return the bytes `[a, b)` of `segment`, shorter when the segment ends earlier."""
        with self._lock:
            return bytes(self._get(segment).data[max(a, 0) : max(b, 0)])

    def row_of(self, segment: int, offset: int) -> int:
        """Return the row inside `segment` that contains byte `offset`."""
        with self._lock:
            return self._get(segment).row_of(offset)

    def row_start(self, segment: int, row: int) -> int | None:
        """Return the start offset of `row` inside `segment`, or `None` beyond the last row."""
        with self._lock:
            return self._get(segment).row_start(row)

    def breaks_between(self, segment: int, a: int, b: int) -> int:
        """Return `row_of(b) - row_of(a)` for boundaries that are not inside a terminator."""
        with self._lock:
            found = self._get(segment)
            return found.row_of(b) - found.row_of(a)

    def _get(self, segment: int) -> _Segment:
        if not 1 <= segment <= len(self._segments):
            raise IndexError(f"no add segment {segment}")
        return self._segments[segment - 1]
