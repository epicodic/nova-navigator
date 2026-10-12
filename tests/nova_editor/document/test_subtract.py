"""`_subtract` and `_subtract_each` of the lazy document: equal to a per-byte model, and linear in the number of ranges."""

from __future__ import annotations

import time
from collections.abc import Sequence

from nova_editor.document import _lazy_document

SCALE = 40_000


def _old_subtract(ranges: Sequence[tuple[int, int]], cut: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """The quadratic implementation that `_subtract` replaced."""
    out: list[tuple[int, int]] = []
    for start, end in ranges:
        position = start
        for cut_start, cut_end in cut:
            if cut_end <= position:
                continue
            if cut_start >= end:
                break
            if cut_start > position:
                out.append((position, cut_start))
            position = max(position, cut_end)
        if position < end:
            out.append((position, end))
    return out


def _runs(mask: int, limit: int) -> list[tuple[int, int]]:
    """The maximal runs of set bits of `mask` below `limit` as ascending, disjoint ranges."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for bit in range(limit + 1):
        if bit < limit and mask >> bit & 1:
            start = bit if start is None else start
        elif start is not None:
            out.append((start, bit))
            start = None
    return out


def _bytes(ranges: Sequence[tuple[int, int]]) -> set[int]:
    return {offset for a, b in ranges for offset in range(a, b)}


LIMIT = 8
"""Every pair of byte sets below `LIMIT` is tried: 65536 pairs."""


def test_subtract_equals_the_per_byte_model_and_the_old_implementation() -> None:
    for left in range(1 << LIMIT):
        ranges = _runs(left, LIMIT)
        for right in range(1 << LIMIT):
            cut = _runs(right, LIMIT)
            result = _lazy_document._subtract(ranges, cut)
            assert result == _old_subtract(ranges, cut), (ranges, cut)
            assert _bytes(result) == _bytes(ranges) - _bytes(cut), (ranges, cut)


def test_subtract_each_gives_the_pieces_of_every_range_in_any_order() -> None:
    for left in range(1 << LIMIT):
        ranges = _runs(left, LIMIT)[::-1]  # copies need not be sorted
        for right in range(0, 1 << LIMIT, 3):
            cut = _runs(right, LIMIT)
            pieces = _lazy_document._subtract_each(ranges, cut)
            assert len(pieces) == len(ranges)
            for item, found in zip(ranges, pieces, strict=True):
                assert found == _old_subtract([item], cut), (item, cut)


def _interleaved() -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    ranges = [(10 * n, 10 * n + 6) for n in range(SCALE)]
    cut = [(10 * n + 2, 10 * n + 3) for n in range(SCALE)]
    return ranges, cut


def test_subtract_scales_linearly() -> None:
    ranges, cut = _interleaved()
    started = time.monotonic()
    result = _lazy_document._subtract(ranges, cut)
    elapsed = time.monotonic() - started
    assert len(result) == 2 * SCALE
    assert elapsed < 2.0, elapsed


def test_subtract_each_scales_linearly() -> None:
    ranges, cut = _interleaved()
    started = time.monotonic()
    pieces = _lazy_document._subtract_each(ranges, cut)
    elapsed = time.monotonic() - started
    assert sum(len(found) for found in pieces) == 2 * SCALE
    assert elapsed < 2.0, elapsed
