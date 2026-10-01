"""Tests for LazyWrappedDocument: stock equivalence for short rows, grid wrap, and the vertical estimate."""

from __future__ import annotations

from collections.abc import Iterator
from math import ceil
from pathlib import Path

import pytest
from textual.expand_tabs import get_tab_widths
from textual.geometry import Offset

from nova_editor.document._document import Document
from nova_editor.document._document_navigator import LazyOffsets
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import MAX_WINDOW_CHARS, LazyDocument, WholeLineAccess
from nova_editor.document._lazy_wrapped_document import ANCHOR_WINDOW, BLOCK_ROWS, LazyWrappedDocument
from nova_editor.document._wrapped_document import WrappedDocument
from tests.nova_editor.helpers_view import LOWERED_OPTIONS, make_mixed, oracle_display_column, oracle_row_ranges, oracle_row_text

TAB = 4
WIDTHS = (10, 20, 40)


def _config() -> LazyConfig:
    return LazyConfig(**LOWERED_OPTIONS)


_OPEN: list[LazyDocument] = []


@pytest.fixture(autouse=True)
def _close_documents() -> Iterator[None]:
    """Close every document a test opened."""
    yield
    while _OPEN:
        _OPEN.pop().close()


@pytest.fixture
def opened(tmp_path: Path) -> Path:
    """The mixed test file (short, medium and long rows)."""
    return make_mixed(tmp_path / "mixed.txt", long_chars=2000)


def build(path: Path, width: int) -> tuple[LazyDocument, LazyWrappedDocument]:
    """Open `path` lazily with the lowered options and wrap it at `width`."""
    doc = LazyDocument.from_path(path, _config())
    _OPEN.append(doc)
    wait(doc)
    return doc, LazyWrappedDocument(doc, width, TAB)


def wait(doc: LazyDocument) -> None:
    """Wait for the line index."""
    assert doc.wait_indexed(10.0)


def row_of_class(doc: LazyDocument, kind: str) -> int:
    """First row of the given class; a long row is settled (its scan joined) before it is returned."""
    row = next(r for r in range(doc.line_count) if doc.row_class(r) == kind)
    if kind == "long":
        assert doc.long_index(row).join(10.0)
    return row


def stock_pair(path: Path, width: int) -> tuple[LazyDocument, LazyWrappedDocument, WrappedDocument]:
    """Lazy and stock wrapped documents over the same text."""
    doc, lazy = build(path, width)
    data = path.read_bytes()
    text = "\n".join(oracle_row_text(data, i) for i in range(len(oracle_row_ranges(data))))
    return doc, lazy, WrappedDocument(Document(text), width, TAB)


def grid_starts(text: str, width: int) -> list[int]:
    """Oracle: first column of every section k >= 0 of the grid wrap (section k covers display starts in [k*W, (k+1)*W))."""
    total = oracle_display_column(text, len(text))
    starts = [0]
    for section in range(1, ceil(total / width) if total else 1):
        target = section * width
        starts.append(next((c for c in range(len(text)) if oracle_display_column(text, c) >= target), len(text)))
    return starts


class TestShortRowsAreStock:
    @pytest.mark.parametrize("width", WIDTHS)
    def test_offsets_sections_and_tab_widths_equal_stock(self, opened: Path, width: int) -> None:
        doc, lazy, stock = stock_pair(opened, width)
        short = [r for r in range(doc.line_count) if doc.row_class(r) == "short"]
        assert len(short) >= 12
        for row in short:
            assert lazy.get_offsets(row) == stock.get_offsets(row)
            assert lazy.get_sections(row) == stock.get_sections(row)
            assert lazy.get_tab_widths(row) == stock.get_tab_widths(row)
            assert lazy.row_sections(row) == len(stock.get_offsets(row)) + 1

    @pytest.mark.parametrize("width", WIDTHS)
    def test_rows_with_tabs_and_cjk_are_wrapped_like_stock(self, opened: Path, width: int) -> None:
        doc, lazy, stock = stock_pair(opened, width)
        assert "日本語" in doc.get_line(10)
        assert lazy.get_offsets(10) == stock.get_offsets(10)

    @pytest.mark.parametrize("width", WIDTHS)
    def test_locations_and_offsets_equal_stock_above_the_first_grid_row(self, opened: Path, width: int) -> None:
        doc, lazy, stock = stock_pair(opened, width)
        first_grid = next(r for r in range(doc.line_count) if doc.row_class(r) != "short")
        for row in range(first_grid):
            for column in range(len(doc.get_line(row)) + 1):
                assert lazy.location_to_offset((row, column)) == stock.location_to_offset((row, column))
        for y in range(lazy.y_of_row(first_grid)):
            for x in range(30):
                assert lazy.offset_to_location(Offset(x, y)) == stock.offset_to_location(Offset(x, y))
        for row in range(first_grid):
            for section in (0, -1):
                for x in range(0, 30, 3):
                    assert lazy.get_target_document_column(row, x, section) == stock.get_target_document_column(row, x, section)

    def test_no_wrap_is_stock_too(self, opened: Path) -> None:
        doc, lazy, stock = stock_pair(opened, 0)
        assert lazy.height == doc.line_count
        for row in range(11):
            assert lazy.get_offsets(row) == []
            for column in range(len(doc.get_line(row)) + 1):
                assert lazy.location_to_offset((row, column)) == stock.location_to_offset((row, column))
            for x in range(0, 40, 3):
                assert lazy.offset_to_location(Offset(x, row)) == stock.offset_to_location(Offset(x, row))
        assert lazy.row_of_y(5) == (5, 0)


class TestGridWrap:
    @pytest.mark.parametrize("kind", ["medium", "long"])
    @pytest.mark.parametrize("width", [7, 20])
    def test_section_starts_follow_display_columns(self, opened: Path, kind: str, width: int) -> None:
        doc, lazy = build(opened, width)
        row = row_of_class(doc, kind)
        text = oracle_row_text(opened.read_bytes(), row)
        expected = grid_starts(text, width)
        assert lazy.row_sections(row) == len(expected)
        assert [lazy.section_start(row, k) for k in range(len(expected))] == expected
        assert lazy.section_start(row, len(expected)) is None
        for column in range(0, len(text) + 1, 5):
            section = min(oracle_display_column(text, column) // width, len(expected) - 1)
            assert lazy.section_of(row, column) == section

    def test_a_char_crossing_a_boundary_belongs_to_the_section_where_it_starts(self, opened: Path) -> None:
        doc, lazy = build(opened, 7)
        row = row_of_class(doc, "long")
        text = oracle_row_text(opened.read_bytes(), row)
        crossed = 0
        for section in range(1, lazy.row_sections(row)):
            start = lazy.section_start(row, section)
            assert start is not None
            assert oracle_display_column(text, start) >= section * 7
            if start > 0 and oracle_display_column(text, start - 1) + (2 if text[start - 1] != "\t" else 1) > section * 7:
                crossed += 1
            assert oracle_display_column(text, start - 1) < section * 7
        assert crossed > 0

    def test_offsets_object_speaks_the_navigator_protocol(self, opened: Path) -> None:
        doc, lazy = build(opened, 20)
        row = row_of_class(doc, "long")
        offsets = lazy.get_offsets(row)
        assert isinstance(offsets, LazyOffsets)
        sections = lazy.row_sections(row)
        assert len(offsets) == sections - 1
        assert bool(offsets)
        starts = [start for k in range(1, sections) if (start := lazy.section_start(row, k)) is not None]
        assert len(starts) == sections - 1
        assert list(offsets) == starts
        assert offsets[0] == starts[0]
        assert offsets[-1] == starts[-1]
        with pytest.raises(IndexError):
            offsets[sections]
        for k, start in enumerate(starts):
            assert offsets.index_of(start) == k
            assert offsets.index_of(start + 1) == -1
            assert offsets.bisect_right(start) == k + 1
            assert offsets.bisect_right(start - 1) == k
        assert offsets.index_of(0) == -1
        assert not offsets.is_last_section(0)
        assert offsets.is_last_section(starts[-1])
        assert offsets.is_last_section(lazy.get_offsets(row)[-1] + 1)

    def test_medium_row_sections_and_tab_widths_do_not_raise(self, opened: Path) -> None:
        doc, lazy = build(opened, 20)
        row = row_of_class(doc, "medium")
        sections = lazy.get_sections(row)
        assert "".join(sections) == doc.get_line(row)
        assert len(sections) == lazy.row_sections(row)
        assert lazy.get_tab_widths(row) == [width for _, width in get_tab_widths(doc.get_line(row), TAB)]

    def test_long_row_whole_line_access_raises(self, opened: Path) -> None:
        doc, lazy = build(opened, 20)
        row = row_of_class(doc, "long")
        with pytest.raises(WholeLineAccess):
            lazy.get_sections(row)
        with pytest.raises(WholeLineAccess):
            lazy.get_tab_widths(row)
        assert doc.call_log.refusals

    def test_long_row_calls_stay_within_the_window_bound(self, opened: Path) -> None:
        doc, lazy = build(opened, 20)
        row = row_of_class(doc, "long")
        for column in range(0, doc.line_length(row) or 0, 17):
            lazy.location_to_offset((row, column))
        for y in range(lazy.y_of_row(row), lazy.y_of_row(row) + lazy.row_sections(row)):
            lazy.offset_to_location(Offset(3, y))
        assert doc.call_log.max_decoded_chars <= MAX_WINDOW_CHARS

    @pytest.mark.parametrize("kind", ["medium", "long"])
    @pytest.mark.parametrize("width", [7, 20])
    def test_location_offset_round_trip(self, opened: Path, kind: str, width: int) -> None:
        doc, lazy = build(opened, width)
        row = row_of_class(doc, kind)
        text = oracle_row_text(opened.read_bytes(), row)
        top = lazy.y_of_row(row)
        for column in range(len(text)):
            x, y = lazy.location_to_offset((row, column))
            section = lazy.section_of(row, column)
            assert section is not None
            assert y == top + section
            assert x >= 0
            if oracle_display_column(text, column + 1) > oracle_display_column(text, column):
                assert lazy.offset_to_location(Offset(x, y)) == (row, column)

    def test_target_column_stays_in_the_section(self, opened: Path) -> None:
        doc, lazy = build(opened, 20)
        row = row_of_class(doc, "long")
        sections = lazy.row_sections(row)
        for section in range(sections - 1):
            start = lazy.section_start(row, section) or 0
            following = lazy.section_start(row, section + 1)
            assert following is not None
            for x in (0, 5, 19, 40):
                assert start <= lazy.get_target_document_column(row, x, section) <= following - 1
        assert lazy.get_target_document_column(row, 0, -1) == lazy.section_start(row, sections - 1)
        assert lazy.get_target_document_column(row, 500, -1) == doc.line_length(row)

    def test_out_of_range_rows_and_read_only_members(self, opened: Path) -> None:
        doc, lazy = build(opened, 20)
        with pytest.raises(ValueError, match="out of bounds"):
            lazy.get_offsets(doc.line_count)
        with pytest.raises(ValueError, match="out of bounds"):
            lazy.get_offsets(-1)
        with pytest.raises(NotImplementedError):
            _ = lazy.lines


class TestVerticalEstimate:
    def test_height_is_exact_without_wrap_and_grows_with_measured_rows(self, opened: Path) -> None:
        doc, lazy = build(opened, 20)
        assert lazy.height >= doc.line_count
        before = lazy.height
        lazy.row_of_y(lazy.height - 1)
        assert lazy.height >= before

    def test_small_document_is_exact_after_measuring_its_only_block(self, opened: Path) -> None:
        doc, lazy = build(opened, 20)
        exact = sum(lazy.row_sections(r) for r in range(doc.line_count))
        assert lazy.height == exact
        assert [lazy.y_of_row(r) for r in range(doc.line_count)] == [sum(lazy.row_sections(q) for q in range(r)) for r in range(doc.line_count)]

    def test_reanchor_keeps_top_row_stable(self, tmp_path: Path) -> None:
        _doc, lazy = build(make_mixed(tmp_path / "m.txt", long_chars=20_000), 20)
        row, _section = lazy.row_of_y(lazy.height // 2)
        lazy.row_of_y(0)
        assert lazy.row_of_y(lazy.y_of_row(row))[0] == row

    def test_row_of_y_inverts_y_of_row_in_a_window_around_the_anchor(self, tmp_path: Path) -> None:
        _doc, lazy = _synthetic(tmp_path, 3000)
        centre = 1500
        lazy.y_of_row(centre)
        for row in range(centre - ANCHOR_WINDOW * BLOCK_ROWS // 2, centre + ANCHOR_WINDOW * BLOCK_ROWS // 2, 7):
            y = lazy.y_of_row(row)
            assert lazy.row_of_y(y) == (row, 0)
            assert lazy.row_of_y(y + lazy.row_sections(row) - 1) == (row, lazy.row_sections(row) - 1)

    def test_window_around_the_anchor_is_exact(self, tmp_path: Path) -> None:
        _doc, lazy = _synthetic(tmp_path, 3000)
        centre = 2000
        lazy.y_of_row(centre)
        for row in range(centre - 100, centre + 100):
            assert lazy.y_of_row(row + 1) - lazy.y_of_row(row) == lazy.row_sections(row)

    def test_measuring_a_block_adjusts_height_but_keeps_order(self, tmp_path: Path) -> None:
        doc, lazy = _synthetic(tmp_path, 3000)
        before = lazy.height
        assert before >= doc.line_count
        lazy.y_of_row(2900)
        after = lazy.height
        assert after >= doc.line_count
        assert lazy.row_of_y(lazy.y_of_row(2900))[0] == 2900
        assert lazy.y_of_row(2900) < after
        assert lazy.y_of_row(10) < lazy.y_of_row(2900)

    def test_far_row_of_y_does_not_read_the_whole_file(self, tmp_path: Path) -> None:
        doc, lazy = _synthetic(tmp_path, 3000)
        row, _section = lazy.row_of_y(lazy.height * 3 // 4)
        assert 1500 < row < 2900
        read = {event.row for event in doc.call_log.events if event.method == "get_line"}
        assert len(read) <= 8 * BLOCK_ROWS
        lazy.y_of_row(20)
        lazy.row_of_y(lazy.height - 1)
        read = {event.row for event in doc.call_log.events if event.method == "get_line"}
        assert len(read) < 3000 // 2

    def test_row_of_y_clamps_beyond_the_end(self, tmp_path: Path) -> None:
        doc, lazy = _synthetic(tmp_path, 200)
        row, section = lazy.row_of_y(10**6)
        assert row == doc.line_count - 1
        assert section == lazy.row_sections(row) - 1
        assert lazy.row_of_y(-5) == (0, 0)

    def test_offset_to_line_info_is_the_lazy_line_info(self, tmp_path: Path) -> None:
        _doc, lazy = _synthetic(tmp_path, 200)
        y = lazy.y_of_row(150) + 1
        assert lazy._offset_to_line_info[y] == lazy.row_of_y(y)
        with pytest.raises(IndexError):
            lazy._offset_to_line_info[lazy.height]
        assert len(lazy._offset_to_line_info) == lazy.height

    def test_note_y_raises_the_height(self, tmp_path: Path) -> None:
        _doc, lazy = _synthetic(tmp_path, 100)
        lazy.note_y(lazy.height + 500)
        assert lazy.height >= 501

    def test_wrap_clears_the_estimate(self, tmp_path: Path) -> None:
        doc, lazy = _synthetic(tmp_path, 300)
        narrow = lazy.height
        lazy.wrap(80)
        assert lazy.height < narrow
        lazy.wrap(0)
        assert lazy.height == doc.line_count

    def test_long_row_estimate_is_refreshed_after_its_scan(self, tmp_path: Path) -> None:
        path = make_mixed(tmp_path / "m.txt", long_chars=20_000)
        doc, lazy = build(path, 20)
        row = next(r for r in range(doc.line_count) if doc.is_long(r))
        lazy.y_of_row(row)
        assert doc.long_index(row).join(10.0)
        lazy.refresh_estimates()
        assert lazy.height == sum(lazy.row_sections(r) for r in range(doc.line_count))


def _synthetic(tmp_path: Path, rows: int) -> tuple[LazyDocument, LazyWrappedDocument]:
    """A file of short rows of varying length so that some wrap at width 20."""
    lines = [f"row {i} " + "word " * (i % 7) for i in range(rows)]
    path = tmp_path / "synthetic.txt"
    path.write_text("\n".join(lines) + "\n")
    return build(path, 20)


# -- bounded measurement of medium rows (REQ-3) --------------------------------------------------
_MEDIUM_ROWS = 1500
_MEASURE_BYTES = 48 * 1024


def _medium_file(path: Path) -> tuple[Path, list[int]]:
    """A file of ASCII rows of 1000 to 1999 bytes (medium under the lowered config); returns it with the row lengths."""
    lengths = [1000 + (i * 37) % 1000 for i in range(_MEDIUM_ROWS)]
    path.write_bytes(b"".join(b"x" * n + b"\n" for n in lengths))
    return path, lengths


def test_medium_rows_are_measured_within_the_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One UI call reads and decodes at most the byte budget, however many medium rows a block holds; the estimate converges."""
    from nova_editor.document import _lazy_wrapped_document as module
    from tests.nova_editor.document.test_lazy_document import CountingSource

    monkeypatch.setattr(module, "MEASURE_MAX_BYTES", _MEASURE_BYTES)
    path, lengths = _medium_file(tmp_path / "medium.txt")
    config = LazyConfig(stride=4, index_long_line_threshold=64, long_row_threshold=2048, word_wrap_limit=128, checkpoint_chars=64)
    source = CountingSource(path)
    doc = LazyDocument(source, config)
    _OPEN.append(doc)
    wait(doc)
    width = 40
    wrapped = LazyWrappedDocument(doc, width, TAB)
    assert doc.row_class(0) == "medium"

    def expected_y(row: int) -> int:
        return sum(-(-n // width) for n in lengths[:row])

    def call(action: str, argument: int = 0) -> None:
        source.owner_reads.clear()
        events = len(doc.call_log.events)
        if action == "row_of_y":
            wrapped.row_of_y(argument)
        elif action == "y_of_row":
            wrapped.y_of_row(argument)
        else:
            assert wrapped.height > 0
        decoded = sum(event.decoded_chars for event in doc.call_log.events[events:])
        assert decoded <= _MEASURE_BYTES, (action, argument, decoded)
        assert sum(source.owner_reads) <= _MEASURE_BYTES + 4 * 65_536, (action, argument, sum(source.owner_reads))

    call("height")
    for _tick in range(400):
        call("y_of_row", 100)
        call("row_of_y", expected_y(100) + 3)
        call("height")
        if not wrapped.pending_refinement:
            break
        wrapped.refresh_estimates()
    assert not wrapped.pending_refinement
    for row in (0, 37, 99, 100, 101, 130):
        assert wrapped.y_of_row(row) == expected_y(row)
        assert wrapped.row_of_y(wrapped.y_of_row(row))[0] == row
    call("y_of_row", 1400)
    call("row_of_y", expected_y(700))


# -- row cap and byte cap of one measuring call ---------------------------------------------------
_CAP_ROWS = 600
_CAP_WIDTH = 40


def _events_since(doc: LazyDocument, start: int, method: str) -> list[int]:
    """Decoded character counts of the `method` calls recorded after event index `start`."""
    return [event.decoded_chars for event in doc.call_log.events[start:] if event.method == method]


def test_one_call_measures_at_most_the_row_cap_of_medium_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wrap-mode file of many medium rows: one call measures at most `MEASURE_MAX_ROWS` of them and later ticks converge."""
    from nova_editor.document import _lazy_wrapped_document as module

    monkeypatch.setattr(module, "MEASURE_MAX_BYTES", 1 << 30)
    lengths = [200 + (i * 13) % 300 for i in range(_CAP_ROWS)]
    path = tmp_path / "medium_rows.txt"
    path.write_bytes(b"".join(b"x" * n + b"\n" for n in lengths))
    doc, wrapped = build(path, _CAP_WIDTH)
    assert all(doc.row_class(row) == "medium" for row in range(_CAP_ROWS))
    assert module.MEASURE_MAX_ROWS < BLOCK_ROWS * 3, "the file must hold more medium rows than the cap in the measured blocks"

    def expected_y(row: int) -> int:
        return sum(-(-n // _CAP_WIDTH) for n in lengths[:row])

    measured_per_call: list[int] = []
    for _tick in range(100):
        for action in (lambda: wrapped.y_of_row(_CAP_ROWS - 1), lambda: wrapped.row_of_y(expected_y(_CAP_ROWS // 2)), lambda: wrapped.height):
            start = len(doc.call_log.events)
            action()
            measured_per_call.append(len(_events_since(doc, start, "row_display_width")))
        if not wrapped.pending_refinement:
            break
        wrapped.refresh_estimates()
    assert not wrapped.pending_refinement
    assert max(measured_per_call) == module.MEASURE_MAX_ROWS, "the cap is reached (the test exercises it) and never exceeded"
    assert sum(1 for count in measured_per_call if count) > 1, "the measurement is spread over several ticks"
    for row in (0, 37, 300, _CAP_ROWS - 1):
        assert wrapped.y_of_row(row) == expected_y(row)


def test_one_call_decodes_at_most_the_byte_budget_of_short_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Short rows are bounded by the byte budget only: with a small budget one call decodes at most that many bytes; ticks converge."""
    from nova_editor.document import _lazy_wrapped_document as module

    budget = 1000
    monkeypatch.setattr(module, "MEASURE_MAX_BYTES", budget)
    lengths = [60 + (i * 7) % 60 for i in range(_CAP_ROWS * 2)]
    path = tmp_path / "short_rows.txt"
    path.write_bytes(b"".join(b"y" * n + b"\n" for n in lengths))
    doc, wrapped = build(path, _CAP_WIDTH)
    assert all(doc.row_class(row) == "short" for row in range(len(lengths)))

    stock = WrappedDocument(Document("\n".join("y" * n for n in lengths)), _CAP_WIDTH, TAB)

    decoded_per_call: list[int] = []
    for _tick in range(400):
        for action in (lambda: wrapped.y_of_row(len(lengths) - 1), lambda: wrapped.height):
            start = len(doc.call_log.events)
            action()
            decoded_per_call.append(sum(_events_since(doc, start, "get_line")))
        if not wrapped.pending_refinement:
            break
        wrapped.refresh_estimates()
    assert not wrapped.pending_refinement
    assert max(decoded_per_call) <= budget
    assert max(decoded_per_call) > 0
    for row in (0, 100, 700, len(lengths) - 2):
        assert wrapped.y_of_row(row + 1) - wrapped.y_of_row(row) == len(stock.get_offsets(row)) + 1


def test_wrap_range_drops_caches_and_remeasures_below_the_edit() -> None:
    doc = LazyDocument.from_text("\n".join(f"row {i} " + "word " * (i % 7) for i in range(BLOCK_ROWS * 4)), _config())
    _OPEN.append(doc)
    lazy = LazyWrappedDocument(doc, 12, TAB)
    top_before = lazy.y_of_row(3)
    lazy.y_of_row(BLOCK_ROWS * 3 + 1)
    kept = {block: lazy._blocks[block] for block in lazy._blocks if block < 2}
    assert lazy._short_rows
    doc.replace_range((BLOCK_ROWS * 2 + 5, 0), (BLOCK_ROWS * 2 + 5, 0), "inserted " * 10 + "\n" + "x\n")
    lazy.wrap_range((BLOCK_ROWS * 2 + 5, 0), (BLOCK_ROWS * 2 + 5, 0), (BLOCK_ROWS * 2 + 7, 0))
    assert not lazy._short_rows
    assert not lazy._disp_cache
    assert all(block < 2 for block in lazy._blocks)
    for block, measured in kept.items():
        assert lazy._blocks[block] is measured
    assert lazy.y_of_row(3) == top_before
    last = doc.line_count - 1
    ys = [lazy.y_of_row(row) for row in range(0, last + 1, 7)]
    assert ys == sorted(ys)
    expected = sum(len(lazy.get_offsets(row)) + 1 for row in range(doc.line_count))
    assert lazy.height >= doc.line_count
    assert abs(lazy.height - expected) <= expected // 5
