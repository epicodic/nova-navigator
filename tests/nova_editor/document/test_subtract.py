"""`_subtract` and `_subtract_each` of the lazy document: equal to a per-byte model, and linear in the number of ranges."""

from __future__ import annotations

import random
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


def _disjoint(rng: random.Random, limit: int, count: int) -> list[tuple[int, int]]:
    """Ascending, disjoint, non-empty ranges (adjacent ones allowed) below `limit`."""
    points = sorted(rng.sample(range(limit + 1), min(2 * count, limit + 1)))
    pairs = list(zip(points[0::2], points[1::2], strict=False))
    return [(a, b) for a, b in pairs if a < b]


def _bytes(ranges: Sequence[tuple[int, int]]) -> set[int]:
    return {offset for a, b in ranges for offset in range(a, b)}


def test_subtract_equals_the_per_byte_model_and_the_old_implementation() -> None:
    rng = random.Random(7)
    for _ in range(400):
        limit = rng.randint(1, 60)
        ranges = _disjoint(rng, limit, rng.randint(0, 8))
        cut = _disjoint(rng, limit, rng.randint(0, 8))
        result = _lazy_document._subtract(ranges, cut)
        assert result == _old_subtract(ranges, cut), (ranges, cut)
        assert _bytes(result) == _bytes(ranges) - _bytes(cut), (ranges, cut)
        assert all(a < b for a, b in result)


def test_subtract_each_gives_the_pieces_of_every_range_in_order() -> None:
    rng = random.Random(11)
    for _ in range(400):
        limit = rng.randint(1, 60)
        ranges = _disjoint(rng, limit, rng.randint(0, 8))
        cut = _disjoint(rng, limit, rng.randint(0, 8))
        rng.shuffle(ranges)  # copies need not be sorted
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
