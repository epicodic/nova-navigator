"""Row-boundary scanner shared by the line index scan and the save thread (design section 6).

Rows end at LF, CRLF or a lone CR; the terminator bytes belong to the row.
"""

from __future__ import annotations

import re

TERMINATOR = re.compile(rb"\r\n|\n|\r")

# (row, start, end) of a row longer than the long-line threshold
LongRow = tuple[int, int, int]


class RowScanner:
    """Row-boundary scanner: feed consecutive blocks, take the new stride entries and long rows after each block."""

    def __init__(self, stride: int, threshold: int) -> None:
        self._stride = stride
        self._threshold = threshold
        self._count = 1
        self._prev = 0
        self._carry = False
        self._entries: list[int] = []  # stride entries found since the last take
        self._longs: list[LongRow] = []  # long rows found since the last take

    @property
    def count(self) -> int:
        """Rows seen so far."""
        return self._count

    @property
    def prev(self) -> int:
        """Start offset of the current row."""
        return self._prev

    def feed(self, block: bytes, base: int, final: bool) -> None:
        """Scan `block`, which starts at absolute offset `base`; `final` marks the last block of the data."""
        begin = 0
        if self._carry:
            self._carry = False
            begin = 1 if block.startswith(b"\n") else 0
            self._boundary(base + begin)
        if block.find(b"\r", begin) == -1:
            self._scan_lf(block, base, begin)
        else:
            self._scan_mixed(block, base, begin, final)

    def take(self) -> tuple[list[int], list[LongRow]]:
        """Return the stride entries and long rows found since the last call, and reset both lists."""
        entries, longs = self._entries, self._longs
        self._entries, self._longs = [], []
        return entries, longs

    def finish(self, length: int) -> list[LongRow]:
        """Return the last row as a long row when it is longer than the threshold: `[(count - 1, prev, length)]`."""
        return [(self._count - 1, self._prev, length)] if length - self._prev > self._threshold else []

    def _boundary(self, nxt: int) -> None:
        """Register the start `nxt` of row `count`, ending the row that began at `prev`."""
        if nxt - self._prev > self._threshold:
            self._longs.append((self._count - 1, self._prev, nxt))
        if self._count % self._stride == 0:
            self._entries.append(nxt)
        self._count += 1
        self._prev = nxt

    def _scan_lf(self, block: bytes, base: int, begin: int) -> None:
        """Fast path for a block without CR: locals only, one threshold comparison per row."""
        stride, threshold = self._stride, self._threshold
        entries, longs = self._entries, self._longs
        count, prev = self._count, self._prev
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
        self._count, self._prev = count, prev

    def _scan_mixed(self, block: bytes, base: int, begin: int, final: bool) -> None:
        for match in TERMINATOR.finditer(block, begin):
            if match.end() == len(block) and not final and match.group() == b"\r":
                self._carry = True  # a CR at the block edge may be the first half of a CRLF
                break
            self._boundary(base + match.end())
