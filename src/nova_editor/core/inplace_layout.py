"""The layout rule of the in-place save: which document ranges differ from what the file holds."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def _add(ranges: list[tuple[int, int]], start: int, end: int) -> None:
    """Append `[start, end)` to the ascending `ranges`, merging it into the last range when they touch."""
    if start >= end:
        return
    if ranges and ranges[-1][1] >= start:
        ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
    else:
        ranges.append((start, end))


def inplace_ranges(runs: Iterable[tuple[int, int, int]], length: int, orig_length: int, patched: Sequence[tuple[int, int]]) -> list[tuple[int, int]] | None:
    """Return the ascending, merged document ranges to write, or `None` when the layout is not an in-place layout.

    `runs` are the `(src, a, b)` runs of the whole document in document order (`PieceTable.layout_iter`), `orig_length` is the length of the
    original file and `patched` the ascending, disjoint ranges of the original that earlier in-place saves overwrote.

    A run of the live original (`src == 0`) is clean when it sits at its own offset (`a` equals its document offset) and lies outside
    `patched`; the part of it inside `patched` is dirty, because the file holds other bytes there than the original did.
    Every other run (an add segment or a legacy generation) is dirty.
    The layout is not an in-place layout when the length differs from `orig_length` or when a run of the original sits at another offset.
    No dirty range at all is valid: the document equals the file.
    """
    if length != orig_length:
        return None
    ranges: list[tuple[int, int]] = []
    pos = 0
    first = 0  # patched[:first] ends before the current run
    for src, a, b in runs:
        size = b - a
        if size <= 0:
            continue
        if src != 0:
            _add(ranges, pos, pos + size)
        else:
            if a != pos:
                return None
            while first < len(patched) and patched[first][1] <= a:
                first += 1
            index = first
            while index < len(patched) and patched[index][0] < b:
                _add(ranges, max(patched[index][0], a), min(patched[index][1], b))
                index += 1
        pos += size
    return ranges
