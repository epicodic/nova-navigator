"""Tests for `OriginalSource` and `BytesSource`."""

from __future__ import annotations

import pytest

from nova_editor.core import BytesSource, LineIndex, OriginalSource, RowNotIndexed, make_piece

from .reference import ALPHABET, FakeSource, Rng, break_ends


def _data(seed: int) -> bytes:
    rng = Rng(seed)
    return b"".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 40)))


def test_bytes_source() -> None:
    source = BytesSource(b"hello")
    assert source.length() == 5
    assert source.read(1, 2) == b"el"
    assert source.read(3, 100, cache=False) == b"lo"
    assert source.read(5, 1) == b""
    assert source.read(9, 1) == b""
    source.close()
    assert source.read(0, 5) == b"hello"
    with pytest.raises(ValueError, match="negative"):
        source.read(-1, 1)


@pytest.mark.parametrize("seed", range(25))
@pytest.mark.parametrize("stride", [1, 2, 64])
def test_original_source_matches_the_reference_when_scan_is_complete(seed: int, stride: int) -> None:
    data = _data(seed)
    source = BytesSource(data)
    index = LineIndex(source, stride=stride, scan_block=7)
    index.scan_now()
    original = OriginalSource(index, source)
    reference = FakeSource(data)
    for offset in range(len(data) + 1):
        if 0 < offset < len(data) and data[offset - 1 : offset + 1] == b"\r\n":
            continue
        assert original.row_of(offset) == reference.row_of(offset)
    for row in range(len(break_ends(data)) + 3):
        assert original.row_start(row) == reference.row_start(row)
    for a in range(0, len(data), 3):
        for b in range(a + 1, len(data) + 1, 2):
            if any(0 < x < len(data) and data[x - 1 : x + 1] == b"\r\n" for x in (a, b)):
                continue
            assert original.breaks_between(a, b) == reference.breaks_between(a, b)
            assert original.read(a, b) == data[a:b]
            assert make_piece(original, 0, a, b) == make_piece(reference, 0, a, b)


def test_unscanned_offsets_raise_and_unknown_rows_are_none() -> None:
    data = b"one\ntwo\nthree\nfour\n"
    source = BytesSource(data)
    index = LineIndex(source, scan_block=4)
    original = OriginalSource(index, source)
    with pytest.raises(RowNotIndexed):
        original.row_of(5)
    assert original.row_start(2) is None
    assert original.read(4, 7) == b"two"


def test_row_start_falls_back_to_the_end_of_the_previous_row_over_the_budget() -> None:
    data = b"short\n" + b"x" * 3000 + b"\nafter\n"
    source = BytesSource(data)
    index = LineIndex(source, stride=1, long_line_threshold=64, long_line_cap=0, read_budget=256, scan_block=64)
    index.scan_now()
    original = OriginalSource(index, source)
    assert index.row_range(1) is None
    assert original.row_start(1) == 6
    assert original.row_start(2) == 3007
    assert original.row_start(99) is None
