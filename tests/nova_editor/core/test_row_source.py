"""`RowSource` and `PieceTable.row_source` against plain bytes (ACT4 design 6.1)."""

from __future__ import annotations

import pytest

from nova_editor.core import AddStore, BytesSource, LineIndex, PieceTable, RowSource, SourceChanged
from nova_editor.core.pieces import Piece

from .reference import Rng

ORIGINAL = b"0123456789abcdefghij\nsecond row\n"


def make_table(original: bytes = ORIGINAL, *, tail_limit: int = 8) -> PieceTable:
    source = BytesSource(original)
    index = LineIndex(source)
    index.scan_now()
    return PieceTable(source, index, AddStore(tail_limit=tail_limit))


def insert(table: PieceTable, at: int, data: bytes) -> None:
    table.splice(at, at, table.add(data))


def test_row_source_reads_across_pieces_like_bytes() -> None:
    table = make_table()
    insert(table, 5, b"XX")
    insert(table, 12, b"yyyyyyyyyyyy")  # larger than the tail limit: its own segment
    table.splice(3, 4, table.content(0, 0))
    expected = table.read(0, table.length)
    assert table.tree.piece_count > 3
    rows = table.row_source(0, table.length)
    assert rows.length() == len(expected)
    rng = Rng(7)
    for _ in range(300):
        offset = rng.randint(0, len(expected) + 2)
        size = rng.randint(0, len(expected) + 2)
        assert rows.read(offset, size) == expected[offset : offset + size]
        assert rows.read(offset, size, cache=False) == expected[offset : offset + size]
    assert rows.read(len(expected), 4) == b""
    with pytest.raises(ValueError, match="negative"):
        rows.read(-1, 1)


def test_row_source_of_a_sub_range_has_row_relative_offsets() -> None:
    table = make_table()
    insert(table, 5, b"XX")
    document = table.read(0, table.length)
    for start, end in [(0, 8), (3, 9), (6, 6), (4, len(document)), (7, 23)]:
        rows = table.row_source(start, end)
        assert rows.length() == end - start
        assert rows.read(0, end - start) == document[start:end]
        assert rows.read(1, 3) == document[start:end][1:4]


def test_row_source_is_a_snapshot() -> None:
    table = make_table()
    insert(table, 5, b"XX")
    before = table.read(0, table.length)
    rows = table.row_source(0, table.length)
    insert(table, 2, b"ZZZZZZZZZZZZZZ")
    table.splice(0, 10, table.content(0, 0))
    insert(table, 0, b"q")  # typing continues the add tail segment
    assert rows.read(0, len(before)) == before


def test_row_source_covers_the_open_tail() -> None:
    source = BytesSource(ORIGINAL)
    index = LineIndex(source, scan_block=4)
    table = PieceTable(source, index, AddStore())
    assert table.has_open_tail
    rows = table.row_source(2, 14)
    assert rows.read(0, 100) == ORIGINAL[2:14]


def test_short_piece_read_raises_source_changed() -> None:
    table = make_table()
    piece = Piece(0, 0, 40, 0, 0, False, False)  # names more bytes than the original has
    rows = RowSource([piece], table.tree._source_of)
    got = rows.read(0, 40)
    assert 0 < len(got) < 40  # a short read is returned as it is
    with pytest.raises(SourceChanged):
        rows.read(len(got), 40)  # reading on from the end of the original finds nothing


def test_empty_row_source_and_close() -> None:
    rows = RowSource([], make_table().tree._source_of)
    assert rows.length() == 0
    assert rows.read(0, 5) == b""
    rows.close()
    rows.close()
