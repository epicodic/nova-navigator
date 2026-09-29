"""Tests of the pure window helper (`widget/_lazy_window.py`) against the reference oracle."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from nova_editor.core import PreadSource
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import MAX_WINDOW_CHARS, LazyDocument
from nova_editor.document._lazy_wrapped_document import LazyWrappedDocument
from nova_editor.widget._lazy_window import WindowText, section_window, window_text
from tests.nova_editor.helpers_view import (
    LOWERED_OPTIONS,
    cells_to_text,
    make_mixed,
    oracle_cells,
    oracle_row_text,
    oracle_section,
    oracle_window,
    wait_frontier,
)


@pytest.fixture
def mixed(tmp_path: Path) -> Iterator[tuple[LazyDocument, bytes, int, int]]:
    """A lazy document over the mixed file, the file bytes, the medium row (11) and the long row (12); row 10 has tabs, CJK and emoji."""
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    doc = LazyDocument(PreadSource(path), LazyConfig(**LOWERED_OPTIONS))
    assert doc.wait_indexed(10)
    assert doc.row_class(11) == "medium"
    assert doc.is_long(12)
    wait_frontier(doc, 12)
    yield doc, path.read_bytes(), 11, 12
    doc.close()


def _shown(window: WindowText, first: int, width: int) -> str:
    """The cells [first, first + width) the widget would show for a window (phantom cells, expand tabs, crop)."""
    cells = oracle_cells(" " * window.phantom_cells + window.text)
    return cells_to_text(cells, window.phantom_cells + first - window.start_disp, width)


@pytest.mark.parametrize("first", [0, 1, 3, 37, 500, 1999, 2048, 4000, 8000])
def test_no_wrap_window_matches_oracle(mixed: tuple[LazyDocument, bytes, int, int], first: int) -> None:
    doc, data, _, long_row = mixed
    width = 40
    window = window_text(doc, long_row, first, width, margin=width)
    assert not window.missing
    assert window.start_disp <= max(0, first - width)
    assert _shown(window, first, width) == oracle_window(data, long_row, first, width)


@pytest.mark.parametrize("row", [9, 10, 11])
def test_short_and_medium_rows_window_match_oracle(mixed: tuple[LazyDocument, bytes, int, int], row: int) -> None:
    doc, data, _, _ = mixed
    for first in (0, 2, 5, 11, 30, 250):
        window = window_text(doc, row, first, 20, margin=20)
        assert _shown(window, first, 20) == oracle_window(data, row, first, 20)
    full = window_text(doc, row, 0, 400, margin=0)
    assert full.text == oracle_row_text(data, row)
    assert full.at_row_end


def test_window_never_shows_a_replacement_character(mixed: tuple[LazyDocument, bytes, int, int]) -> None:
    doc, data, _, long_row = mixed
    text = oracle_row_text(data, long_row)
    for first in range(400):
        window = window_text(doc, long_row, first, 7, margin=3)
        assert "�" not in window.text
        assert window.text == text[window.start_column : window.start_column + len(window.text)]


def test_window_is_bounded(mixed: tuple[LazyDocument, bytes, int, int]) -> None:
    doc, _, _, long_row = mixed
    window = window_text(doc, long_row, 0, 100_000, margin=100_000)
    assert len(window.text) <= MAX_WINDOW_CHARS
    assert doc.call_log.max_decoded_chars <= MAX_WINDOW_CHARS


def test_window_near_row_end_reports_end(mixed: tuple[LazyDocument, bytes, int, int]) -> None:
    doc, data, _, long_row = mixed
    text = oracle_row_text(data, long_row)
    last = oracle_cells(text)
    window = window_text(doc, long_row, len(last) - 5, 10, margin=5)
    assert window.at_row_end
    assert not window.truncated


class _Frontier:
    """Fake row source whose text is only known up to `known` characters (ASCII, tab-free)."""

    def __init__(self, text: str, known: int) -> None:
        self.text = text
        self.known = known

    def line_length(self, row: int) -> int | None:
        return len(self.text) if self.known >= len(self.text) else None

    def column_slice(self, row: int, start: int, stop: int) -> str:
        return self.text[start : min(stop, self.known)]

    def display_column(self, row: int, column: int, tab_width: int = 4) -> int | None:
        return column if column <= self.known else None

    def column_at_display(self, row: int, x: int, tab_width: int = 4) -> int | None:
        return x if x < self.known else None


def test_window_beyond_the_frontier_is_missing_and_partial_window_is_truncated() -> None:
    source = _Frontier("x" * 1000, known=100)
    assert window_text(source, 0, 500, 20, margin=20).missing
    partial = window_text(source, 0, 90, 20, margin=20)
    assert not partial.missing
    assert partial.truncated
    assert partial.text == "x" * 30
    assert partial.start_column == 70
    whole = window_text(_Frontier("abc", known=3), 0, 0, 20, margin=20)
    assert whole.at_row_end
    assert not whole.truncated


@pytest.mark.parametrize("row_kind", ["medium", "long"])
def test_section_window_matches_oracle(mixed: tuple[LazyDocument, bytes, int, int], row_kind: str) -> None:
    doc, data, medium, long_row = mixed
    row = medium if row_kind == "medium" else long_row
    grid = 23
    wrapped = LazyWrappedDocument(doc, width=grid, tab_width=4)
    sections = wrapped.row_sections(row)
    for section in [0, 1, 2, sections // 2, sections - 1]:
        window = section_window(doc, wrapped, row, section)
        assert not window.missing
        assert _shown(window, window.start_disp, grid + 2) == oracle_section(data, row, section, grid, grid + 2)
    last = section_window(doc, wrapped, row, sections - 1)
    assert last.at_row_end
    assert section_window(doc, wrapped, row, sections + 3).missing


def test_window_with_combining_marks_and_tabs(tmp_path: Path) -> None:
    text = "éa\tb中" * 200
    path = tmp_path / "c.txt"
    path.write_bytes(text.encode())
    doc = LazyDocument(PreadSource(path), LazyConfig(**LOWERED_OPTIONS))
    try:
        assert doc.wait_indexed(10)
        assert doc.is_long(0)
        wait_frontier(doc, 0)
        data = path.read_bytes()
        for first in (0, 1, 2, 5, 33, 100, 777):
            window = window_text(doc, 0, first, 12, margin=6)
            assert _shown(window, first, 12) == oracle_window(data, 0, first, 12)
    finally:
        doc.close()
