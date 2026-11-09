"""`PieceTable.layout_iter`: the lazy sibling of `layout_range` that neither fills nor reads."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from nova_editor.core import AddStore, Content, LineIndex, PieceTable, make_piece

from .reference import ORIGINALS, StepSource
from .test_piece_table import Harness

SEEDS = range(6)
STEPS = 60
LINES = b"alpha\nbeta\r\ngamma\rdelta\nepsilon\n" * 4


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("name", ["lf", "mixed", "no-trailing"])
@pytest.mark.parametrize("scan_block", [None, 8])
def test_layout_iter_equals_layout_range(name: str, scan_block: int | None, seed: int) -> None:
    harness = Harness(ORIGINALS[name], scan_block=scan_block, seed=seed)
    try:
        for _ in range(STEPS):
            harness.random_step()
            table = harness.table
            assert list(table.layout_iter(0, table.length)) == table.layout_range(0, table.length)
            for _ in range(3):
                start = harness.rng.randint(0, table.length)
                end = harness.rng.randint(start, table.length)
                assert list(table.layout_iter(start, end)) == table.layout_range(start, end)
    finally:
        harness.stop()


def test_layout_iter_is_lazy_and_empty_for_an_empty_range() -> None:
    harness = Harness(ORIGINALS["lf"], scan_block=None, seed=1)
    runs = harness.table.layout_iter(3, 3)
    assert isinstance(runs, Iterator)
    assert list(runs) == []
    assert isinstance(harness.table.layout_iter(0, harness.table.length), Iterator)


def test_layout_iter_rejects_an_invalid_range() -> None:
    harness = Harness(ORIGINALS["lf"], scan_block=None, seed=1)
    with pytest.raises(ValueError, match="outside"):
        list(harness.table.layout_iter(0, harness.table.length + 1))


def test_layout_iter_leaves_unknown_pieces_alone() -> None:
    source = StepSource(LINES, 8)
    index = LineIndex(source, stride=2, scan_block=8)
    source.attach(index)
    store = AddStore(tail_limit=24, stride=2)
    table = PieceTable(source, index, store, 4)
    try:
        source.step()
        segment, a, b = store.append(b"NEW")
        piece = make_piece(store.segment(segment), segment, a, b)
        table.splice(0, 5, Content.from_pieces([piece], 3))
        assert table.has_unknown
        unknown = table.tree.unknown
        count = table.tree.piece_count
        before = table.layout_range(0, table.length)
        assert list(table.layout_iter(0, table.length)) == before
        assert (table.tree.unknown, table.tree.piece_count) == (unknown, count)
        assert table.layout_range(0, table.length) == before
    finally:
        source.stop()
