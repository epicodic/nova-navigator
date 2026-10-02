"""Mapping from the saved file back to the pieces it was written from (design section 8.2; completed by a later task)."""

from __future__ import annotations

from array import array
from heapq import heappop, heappush
from itertools import pairwise


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
        rows = [k for k in range(len(self._src)) if self._src[k] == src]  # in add order, i.e. ascending output offset
        starts = self._a
        by_start = sorted(rows, key=lambda k: starts[k])
        bounds = sorted({value for k in rows for value in (self._a[k], self._b[k])})
        heap: list[tuple[int, int]] = []  # (rank = add order, end) of the runs that cover the sweep position
        result: list[tuple[int, int, int]] = []
        owner = -1  # the run the last result interval came from
        cursor = 0
        for x, next_x in pairwise(bounds):
            while cursor < len(by_start) and self._a[by_start[cursor]] <= x:
                k = by_start[cursor]
                heappush(heap, (k, self._b[k]))
                cursor += 1
            while heap and heap[0][1] <= x:
                heappop(heap)
            if not heap:
                continue
            k = heap[0][0]
            out = self._out[k] + x - self._a[k]
            if k == owner and result[-1][1] == x:
                result[-1] = (result[-1][0], next_x, result[-1][2])
            else:
                result.append((x, next_x, out))
                owner = k
        return result
