"""`PieceTable` with unknown pieces: junctions, the fill, blind edits and the table invariants (ACT1 design 5 to 8, 11.3, 11.4)."""

from __future__ import annotations

import pytest

from nova_editor.core import AddStore, BytesSource, Content, LineIndex, PieceTable, make_unknown_piece
from nova_editor.core.piece_table import FILL_BATCH

from .helpers import CountingSource, reference_rows
from .reference import inside_crlf
from .test_piece_table import Harness, StepSource

EMPTY = Content.from_pieces((), 0)


@pytest.mark.parametrize("steps", range(4))
def test_cr_known_then_lf_unknown_counts_once(steps: int) -> None:
    harness = Harness(b"\nab\r\ncd\r\nef\r\n", scan_block=3, seed=2)
    try:
        assert harness.stepper is not None
        for _ in range(steps):
            harness.stepper.step()
        harness.splice(0, 0, harness.from_store(b"\r"), b"\r")
        count = harness.table.snapshot().count
        assert count <= len(reference_rows(harness.ref))
        harness.splice(0, 1, EMPTY, b"")
        harness.finish_scan()
        assert harness.table.snapshot().count == len(reference_rows(harness.ref))
    finally:
        harness.stop()


def test_lf_known_after_cr_unknown() -> None:
    harness = Harness(b"xx\ryy\r\nzz", scan_block=3, seed=3)
    try:
        content, data = harness.from_original(0, 3)  # "xx\r" beyond the frontier: an unknown piece that ends in a CR
        assert content.unknown == 1
        harness.splice(0, 0, content, data)
        harness.splice(3, 3, harness.from_store(b"\n"), b"\n")  # the LF is known, the CR before it is not
        with pytest.raises(ValueError, match="CRLF"):
            harness.table.splice(3, 3, harness.from_store(b"q"))
        harness.splice(2, 2, harness.from_store(b"q"), b"q")
        harness.finish_scan()
        assert harness.table.snapshot().count == len(reference_rows(harness.ref))
    finally:
        harness.stop()


def test_deleting_a_range_that_joins_cr_and_lf_in_the_unknown_region() -> None:
    harness = Harness(b"a\rXX\nb\r\nc", scan_block=3, seed=4)
    try:
        harness.splice(2, 4, EMPTY, b"")  # "a\r" and "\nb\r\nc" meet: one CRLF
        assert harness.ref == b"a\r\nb\r\nc"
        harness.finish_scan()
        snap = harness.table.snapshot()
        assert snap.complete
        assert snap.count == len(reference_rows(harness.ref)) == 3
    finally:
        harness.stop()


def test_undo_reinserts_stale_unknown_pieces() -> None:
    harness = Harness(b"alpha\nbeta\n\ngamma\ndelta\nepsilon\nzeta\n", scan_block=6, seed=5)
    try:
        before = harness.ref
        harness.splice(10, 20, EMPTY, b"")
        removed, data = harness.saved[-1]
        assert removed.unknown > 0
        harness.splice(10, 10, removed, data)
        assert harness.ref == before
        assert harness.table.read(0, len(before)) == before
        harness.finish_scan()
        assert harness.table.snapshot().complete
        assert harness.table.tree.unknown == 0
    finally:
        harness.stop()


@pytest.mark.parametrize("scan_block", [1, 2, 3, 4])
def test_crlf_split_at_the_frontier(scan_block: int) -> None:
    harness = Harness(b"a\r\nb\r\n", scan_block=scan_block, seed=6)
    try:
        harness.splice(0, 0, harness.from_store(b"Z"), b"Z")
        assert harness.stepper is not None
        while not harness.stepper.finished:
            harness.stepper.step()
            harness.check()
            offset = 0
            for piece in harness.table.tree.pieces():
                assert not inside_crlf(harness.ref, offset)
                offset += piece.length
    finally:
        harness.stop()


def test_fill_keeps_the_piece_count_flat() -> None:
    data = b"line\n" * 2000
    source = StepSource(data, 40)
    index = LineIndex(source, stride=2, scan_block=40)
    source.attach(index)
    table = PieceTable(source, index, AddStore(), 4)
    try:
        table.splice(0, 0, table.add(b"X"))
        assert table.tree.piece_count == 2
        counts: list[int] = []
        while not source.finished:
            source.step()
            table.snapshot()
            counts.append(table.tree.piece_count)
        for _ in range(3):
            table.snapshot()
        assert max(counts) <= 4
        assert table.tree.piece_count == 2
        assert not table.has_unknown
        assert table.snapshot().complete
        assert table.read(0, table.length) == b"X" + data
    finally:
        source.stop()


def test_fill_is_batched() -> None:
    original = b"0123456789\n" * 30
    harness = Harness(original, scan_block=7, seed=7)
    try:
        table = harness.table
        for i in range(200):
            piece = make_unknown_piece(table.source_of(0), 0, i, i + 1)
            table.splice(table.length, table.length, Content.from_pieces([piece], 1))
            table.splice(table.length, table.length, table.add(b"y"))
        assert harness.stepper is not None
        while not harness.stepper.finished:
            harness.stepper.step()
        before = table.tree.unknown
        assert before >= 200
        assert not table.snapshot().complete
        assert table.tree.unknown == before - FILL_BATCH
        calls = 1
        while table.tree.unknown:
            assert not table.snapshot().complete or table.tree.unknown == 0
            calls += 1
            assert calls <= -(-before // FILL_BATCH) + 1
        assert table.snapshot().complete
        harness.ref = original + b"".join(original[i : i + 1] + b"y" for i in range(200))
        harness.check()
    finally:
        harness.stop()


def test_far_edit_on_unstarted_scan_reads_nothing_more_than_the_boundary_bytes() -> None:
    data = b"abc\n" * 4000
    counting = CountingSource(BytesSource(data))
    index = LineIndex(counting, scan_block=4096)  # never started: the frontier stays 0
    table = PieceTable(counting, index, AddStore())
    length = len(data)
    table.splice(length, length, table.add(b"Z"))
    table.splice(length - 1, length, EMPTY)
    assert counting.bytes_read <= 64
    assert not table.snapshot().complete
    assert table.row_range(1) is None
    assert table.read(length - 3, 10) == b"bc" + b"Z"
    assert table.length == length


def test_open_row_is_unavailable_but_earlier_rows_resolve() -> None:
    harness = Harness(b"a\nbb\nccc\ndddd\neeeee\nffffff\n", scan_block=5, seed=8)
    try:
        assert harness.stepper is not None
        harness.stepper.step()
        harness.stepper.step()
        end = len(harness.ref)
        harness.splice(end, end, harness.from_store(b"Q"), b"Q")
        table = harness.table
        snap = table.snapshot()
        assert not snap.complete
        lead = snap.count - 1
        assert lead >= 1
        expected = reference_rows(harness.ref)
        for row in range(lead):
            found = table.row_range(row)
            assert found is not None
            assert (found.start, found.content_end, found.end) == expected[row]
        assert table.row_range(lead) is None
        assert table.row_at_offset(table.length) is None
    finally:
        harness.stop()


def test_complete_requires_no_unknown_and_a_finished_scan() -> None:
    harness = Harness(b"abc\ndef", scan_block=4, seed=9)
    try:
        assert harness.stepper is not None
        harness.stepper.step()
        harness.splice(4, 7, EMPTY, b"")
        table = harness.table
        snap = table.snapshot()
        assert table.tree.unknown == 0
        assert not snap.complete
        harness.finish_scan()
        snap = table.snapshot()
        assert snap.complete
        assert snap.count == len(reference_rows(harness.ref))
    finally:
        harness.stop()


def test_check_invariants_of_the_table() -> None:
    harness = Harness(b"alpha\nbeta\ngamma\n", scan_block=None, seed=10)
    table = harness.table
    harness.splice(0, 0, harness.from_store(b"Z"), b"Z")
    table.check_invariants()
    table.tree.split_at(5)  # two contiguous known pieces of the original: not merged
    with pytest.raises(AssertionError, match="mergeable"):
        table.check_invariants()


def test_check_invariants_rejects_an_unknown_piece_below_the_frontier() -> None:
    harness = Harness(b"alpha\nbeta\ngamma\n", scan_block=None, seed=11)
    table = harness.table
    harness.splice(0, 0, harness.from_store(b"Z"), b"Z")
    table.check_invariants()
    length = len(harness.original)
    table.tree.replace_piece(1, make_unknown_piece(table.source_of(0), 0, 0, length))
    with pytest.raises(AssertionError, match="below the frontier"):
        table.check_invariants()
