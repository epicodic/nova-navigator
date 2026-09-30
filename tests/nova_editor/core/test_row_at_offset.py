"""Tests for `LineIndex.row_at_offset` and `LineSnapshot.scanned_bytes`."""

from __future__ import annotations

import threading

from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.core.line_index import DEFAULT_LONG_LINE_THRESHOLD, DEFAULT_STRIDE, LineIndex, RowRange

from .helpers import ATOMS, CountingSource, MemorySource, reference_row_at_offset


def _built(data: bytes, stride: int = DEFAULT_STRIDE, long_line_threshold: int = DEFAULT_LONG_LINE_THRESHOLD) -> LineIndex:
    index = LineIndex(MemorySource(data), stride=stride, long_line_threshold=long_line_threshold)
    index.start()
    assert index.join(10)
    return index


@settings(max_examples=200, deadline=None)
@given(
    atoms=st.lists(st.sampled_from(ATOMS), max_size=60),
    stride=st.sampled_from([1, 2, 4, 64]),
    threshold=st.sampled_from([1, 3, 16384]),
    fraction=st.floats(0, 1),
)
def test_row_at_offset_matches_reference(atoms: list[bytes], stride: int, threshold: int, fraction: float) -> None:
    data = b"".join(atoms)
    index = _built(data, stride=stride, long_line_threshold=threshold)
    offset = round(fraction * len(data))
    row, (start, content_end, end) = reference_row_at_offset(data, offset)
    assert index.row_at_offset(offset) == (row, RowRange(start, content_end, end))


def test_every_offset_of_a_crlf_file() -> None:
    data = b"ab\r\ncd\ref\n\n"
    index = _built(data, stride=2)
    for offset in range(len(data) + 1):
        row, (start, content_end, end) = reference_row_at_offset(data, offset)
        assert index.row_at_offset(offset) == (row, RowRange(start, content_end, end))


def test_offset_out_of_bounds_is_none() -> None:
    index = _built(b"a\nb\n")
    assert index.row_at_offset(-1) is None
    assert index.row_at_offset(5) is None


def test_row_at_offset_none_beyond_scanned() -> None:
    index = LineIndex(MemorySource(b"a\n" * 100))
    assert index.snapshot().scanned_bytes == 0
    assert index.row_at_offset(50) is None
    assert index.row_at_offset(0) is None


def test_scanned_bytes_reaches_length() -> None:
    data = b"x\r\ny\rz"
    index = _built(data)
    assert index.snapshot().scanned_bytes == len(data)


def test_scanned_bytes_grows_while_scanning_and_answers_inside_it() -> None:
    data = b"row\n" * 100
    gate = threading.Event()
    index = LineIndex(MemorySource(data, gate=gate), scan_block=40)
    assert index.snapshot().scanned_bytes == 0
    gate.set()
    index.start()
    assert index.join(10)
    assert index.snapshot().scanned_bytes == len(data)
    assert index.row_at_offset(len(data)) == (100, RowRange(400, 400, 400))


def test_partial_scan_answers_only_indexed_offsets() -> None:
    data = b"row\n" * 100
    calls = 0

    class Stopping(MemorySource):
        def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
            nonlocal calls
            if not cache:
                calls += 1
                if calls > 1:
                    raise_stop.wait(10)
            return super().read(offset, size, cache=cache)

    raise_stop = threading.Event()
    index = LineIndex(Stopping(data), scan_block=40, stride=2)
    index.start()
    while index.snapshot().scanned_bytes == 0:
        threading.Event().wait(0.001)
    snap = index.snapshot()
    assert not snap.complete
    assert 0 < snap.scanned_bytes <= 40
    assert index.row_at_offset(snap.scanned_bytes - 1) is not None
    assert index.row_at_offset(snap.scanned_bytes) is None
    assert index.row_at_offset(len(data)) is None
    raise_stop.set()
    index.join(10)


def test_row_at_offset_respects_budget() -> None:
    data = b"a" * 200_000 + b"\n" + b"b\n" * 10
    source = CountingSource(MemorySource(data))
    index = LineIndex(source, read_budget=1024, long_line_threshold=100_000, long_line_cap=0)
    index.start()
    assert index.join(10)
    before = source.bytes_read
    assert index.row_at_offset(150_000) is None
    assert source.bytes_read - before <= 1024 + 2


def test_recorded_long_row_is_crossed_without_reading_it() -> None:
    data = b"a" * 200_000 + b"\r\n" + b"b\n" * 10
    source = CountingSource(MemorySource(data))
    index = LineIndex(source, read_budget=1024, long_line_threshold=100_000)
    index.start()
    assert index.join(10)
    before = source.bytes_read
    assert index.row_at_offset(150_000) == (0, RowRange(0, 200_000, 200_002))
    assert index.row_at_offset(200_001) == (0, RowRange(0, 200_000, 200_002))
    assert index.row_at_offset(200_002) == (1, RowRange(200_002, 200_003, 200_004))
    assert source.bytes_read - before <= 3 * 1024
