"""Mapping from the saved file back to the pieces it was written from (design section 8.2; completed by a later task)."""

from __future__ import annotations

from array import array
from bisect import bisect_right


class SaveLayout:
    """Records, for every written part, the source it came from, its range there and its offset in the new file."""

    def __init__(self) -> None:
        self._src = array("L")
        self._a = array("Q")
        self._b = array("Q")
        self._out = array("Q")

    def add(self, src: int, a: int, b: int, out: int) -> None:
        """Append the part `[a, b)` of source `src` written at offset `out` of the new file."""
        self._src.append(src)
        self._a.append(a)
        self._b.append(b)
        self._out.append(out)

    def __len__(self) -> int:
        return len(self._src)

    def intervals(self, src: int) -> list[tuple[int, int, int]]:
        """Return the ranges of source `src` present in the new file as disjoint ascending `(a, b, out)`; byte `x` of `[a, b)` is at `out + x - a`.

        A source range written several times (an internal copy-paste) keeps its first occurrence, the one with the lowest output offset.
        """
        starts: list[int] = []
        ends: list[int] = []
        outs: list[int] = []
        for k in range(len(self._src)):
            if self._src[k] != src:
                continue
            a, b, out = self._a[k], self._b[k], self._out[k]
            index = bisect_right(ends, a)
            cursor = a
            gaps: list[tuple[int, int, int, int]] = []  # (insert position, a, b, out)
            while index < len(starts) and starts[index] < b:
                if starts[index] > cursor:
                    gaps.append((index, cursor, starts[index], out + cursor - a))
                cursor = max(cursor, ends[index])
                index += 1
            if cursor < b:
                gaps.append((index, cursor, b, out + cursor - a))
            for position, gap_a, gap_b, gap_out in reversed(gaps):
                starts.insert(position, gap_a)
                ends.insert(position, gap_b)
                outs.insert(position, gap_out)
        return list(zip(starts, ends, outs, strict=True))
