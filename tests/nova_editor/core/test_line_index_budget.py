"""Tests for the long-row side table and the per-call byte budget (DEC-14, design 5.4)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.core.byte_source import PreadSource
from nova_editor.core.line_index import LineIndex, RowRange

from .helpers import CountingSource, MemorySource, reference_rows

MIB = 1024 * 1024
MEDIUM_FILE_SIZE = 64 * MIB
MEDIUM_BYTES_BOUND = 3_300_000
MEDIUM_TIME_BOUND = 0.05
ROW_SAMPLES = 200
BATCH_SAMPLES = 50
BATCH_ROWS = 128


def lcg(seed: int) -> Iterator[int]:
    """Deterministic pseudo-random sequence (a linear congruential generator); the tests need repeatable spread, not entropy."""
    state = seed
    while True:
        state = (state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        yield state >> 33


def line_lengths(threshold: int) -> st.SearchStrategy[int]:
    return st.one_of(
        st.integers(0, threshold - 1),
        st.just(threshold),
        st.just(threshold + 1),
        st.integers(threshold + 2, 4 * threshold),
    )


@st.composite
def medium_files(draw: st.DrawFn) -> tuple[bytes, int]:
    threshold = draw(st.integers(4, 64))
    lengths = draw(st.lists(line_lengths(threshold), min_size=1, max_size=40))
    endings = draw(st.lists(st.sampled_from([b"\n", b"\r\n", b"\r"]), min_size=len(lengths), max_size=len(lengths)))
    data = b"".join(b"x" * n + e for n, e in zip(lengths, endings, strict=True))
    if draw(st.booleans()):
        data += b"tail"
    return data, threshold


def bound(stride: int, m: int, threshold: int, window: int) -> int:
    """Per-call byte bound: rows walked (`stride - 1 + m`) times the cost of one row, plus two tail bytes per emitted row.

    The walk fills `fill = min(window, threshold + 2)` bytes at a time and every unrecorded row is at most `threshold + 2` bytes.
    A run of `k` rows between recorded rows costs at most `k * fill` when a fill covers one whole row, else `k * (threshold + 2 + fill)`.
    (The design's `two windows of alignment` term is wrong: every jump over a recorded row wastes the rest of a fill.)
    """
    fill = min(window, threshold + 2)
    per_row = fill if window >= threshold + 2 else threshold + 2 + fill
    return (stride - 1 + m) * per_row + 2 * m


@given(
    spec=medium_files(),
    stride=st.integers(1, 4),
    m=st.integers(1, 6),
    window=st.integers(8, 32),
    block=st.sampled_from([3, 16, 1 << 20]),
)
@settings(deadline=None, max_examples=300)
def test_calls_stay_within_the_byte_bound_and_are_correct(spec: tuple[bytes, int], stride: int, m: int, window: int, block: int) -> None:
    data, threshold = spec
    source = MemorySource(data)
    limit = bound(stride, m, threshold, window)
    index = LineIndex(
        source,
        stride=stride,
        long_line_threshold=threshold,
        long_line_cap=10_000,
        read_budget=limit,
        max_lines_per_call=m,
        scan_block=block,
        walk_window=window,
    )
    index.start()
    assert index.join(10)
    rows = reference_rows(data)
    for row, expected in enumerate(rows):
        source.reset_counters()
        assert index.row_range(row) == RowRange(*expected)  # cap is large: never unresolved
        assert source.bytes_read <= limit
    for first in range(0, len(rows), m):
        source.reset_counters()
        assert index.lines(first, m) == [RowRange(*r) for r in rows[first : first + m]]
        assert source.bytes_read <= limit


@given(spec=medium_files(), stride=st.integers(1, 4), budget=st.integers(1, 200), cap=st.integers(0, 3))
@settings(deadline=None, max_examples=300)
def test_tiny_budget_and_cap_never_return_a_wrong_range(spec: tuple[bytes, int], stride: int, budget: int, cap: int) -> None:
    data, threshold = spec
    source = MemorySource(data)
    index = LineIndex(
        source,
        stride=stride,
        long_line_threshold=threshold,
        long_line_cap=cap,
        read_budget=budget,
        walk_window=8,
        scan_block=16,
    )
    index.start()
    assert index.join(10)
    rows = reference_rows(data)
    for row, expected in enumerate(rows):
        source.reset_counters()
        got = index.row_range(row)
        assert got is None or got == RowRange(*expected)
        assert source.bytes_read <= budget
    source.reset_counters()
    prefix = index.lines(0, len(rows))
    assert prefix == [RowRange(*r) for r in rows[: len(prefix)]]
    assert source.bytes_read <= budget


def test_recorded_long_rows_cost_no_walk_reads() -> None:
    data = b"a\n" + b"L" * 1000 + b"\n" + b"b\n"
    source = MemorySource(data)
    index = LineIndex(source, stride=1, long_line_threshold=100, walk_window=16)
    index.start()
    assert index.join(10)
    assert index.long_row_count == 1
    source.reset_counters()
    assert index.row_range(1) == RowRange(2, 1002, 1003)
    assert source.bytes_read <= 2  # only the two terminator bytes of the tail check


def test_long_rows_after_the_cap_are_unresolved_not_wrong() -> None:
    data = b"".join(b"L" * 50 + b"\n" for _ in range(5))
    index = LineIndex(MemorySource(data), stride=8, long_line_threshold=10, long_line_cap=2, read_budget=40, walk_window=16)
    index.start()
    assert index.join(10)
    assert index.long_row_count == 2
    assert index.long_overflow
    assert index.row_range(0) == RowRange(0, 50, 51)
    assert index.row_range(1) == RowRange(51, 101, 102)
    assert index.row_range(4) is None  # walk through three unrecorded 51-byte rows exceeds the 40-byte budget


def build_medium_file(path: Path, kind: str) -> None:
    numbers = lcg(7)
    with path.open("wb") as out:
        written = 0
        while written < MEDIUM_FILE_SIZE:
            if kind == "below":
                length = 15_000
            elif kind == "above":
                length = 17_000
            else:
                length = (15_000, 15_000, 17_000, MIB + 5)[next(numbers) % 4]
            out.write(b"x" * length + b"\n")
            written += length + 1


def test_medium_long_lines_are_bounded_in_bytes_and_time(tmp_path: Path) -> None:
    worst_bytes = 0
    worst_time = 0.0
    for kind in ("below", "above", "mixed"):
        path = tmp_path / f"{kind}.txt"
        build_medium_file(path, kind)
        source = CountingSource(PreadSource(path))
        index = LineIndex(source)
        index.start()
        assert index.join(60)
        rows = index.snapshot().count  # complete: exact, the last row is the empty row after the final LF
        numbers = lcg(1)
        for _ in range(ROW_SAMPLES):
            row = next(numbers) % rows
            source.bytes_read = 0
            began = time.perf_counter()
            assert index.row_range(row) is not None
            worst_time = max(worst_time, time.perf_counter() - began)
            worst_bytes = max(worst_bytes, source.bytes_read)
            assert source.bytes_read <= MEDIUM_BYTES_BOUND
        for _ in range(BATCH_SAMPLES):
            first = next(numbers) % rows
            source.bytes_read = 0
            began = time.perf_counter()
            got = index.lines(first, BATCH_ROWS)
            worst_time = max(worst_time, time.perf_counter() - began)
            worst_bytes = max(worst_bytes, source.bytes_read)
            assert len(got) == min(BATCH_ROWS, rows - first)
            assert source.bytes_read <= MEDIUM_BYTES_BOUND
        assert worst_time < MEDIUM_TIME_BOUND, f"{kind}: worst call {worst_time * 1000:.1f} ms"
    print(f"medium-line worst: {worst_bytes} bytes, {worst_time * 1000:.2f} ms")
