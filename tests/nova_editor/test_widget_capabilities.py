"""Tests that the widget reads rows through the DocumentBase capability methods (stock behaviour unchanged)."""

from __future__ import annotations

import itertools
import re
from pathlib import Path

from textual._cells import cell_len, cell_width_to_column_index
from textual.expand_tabs import expand_tabs_inline
from textual.geometry import clamp

from nova_editor.document._document import DocumentBase, Selection
from nova_editor.widget import NovaTextArea, _text_area

_WORD_PATTERN = re.compile(r"(?<=\W)(?=\w)|(?<=\w)(?=\W)")
_ALPHABET = ["a", "_", " ", "\t", "-", "\u4e2d", "e\u0301", "\U0001f600"]


def test_widget_has_no_direct_whole_row_reads() -> None:
    """The vendored widget must not read whole rows outside the capability methods."""
    source = Path(_text_area.__file__).read_text()
    for needle in ("self.document[row][", "len(self.document[", "not self.text and self.placeholder"):
        assert needle not in source, needle


# -- reference copies of the original method bodies (git show 536a7e1) --------------------------------------------


def _ref_word_left(document: DocumentBase, location: tuple[int, int]) -> tuple[int, int]:
    cursor_row, cursor_column = location
    if cursor_row > 0 and cursor_column == 0:
        return cursor_row - 1, len(document[cursor_row - 1])
    line = document[cursor_row][:cursor_column]
    search_string = line.rstrip()
    matches = list(re.finditer(_WORD_PATTERN, search_string))
    return cursor_row, matches[-1].start() if matches else 0


def _ref_word_right(document: DocumentBase, location: tuple[int, int]) -> tuple[int, int]:
    cursor_row, cursor_column = location
    line = document[cursor_row]
    if cursor_row < document.line_count - 1 and cursor_column == len(line):
        return cursor_row + 1, 0
    search_string = line[cursor_column:]
    pre_strip_length = len(search_string)
    search_string = search_string.lstrip()
    strip_offset = pre_strip_length - len(search_string)
    matches = list(re.finditer(_WORD_PATTERN, search_string))
    if matches:
        cursor_column += matches[0].start() + strip_offset
    else:
        cursor_column = len(line)
    return cursor_row, cursor_column


def _ref_cell_width_to_column_index(document: DocumentBase, cell_width: int, row: int, indent: int) -> int:
    return cell_width_to_column_index(document[row], cell_width, indent)


def _ref_column_width(document: DocumentBase, row: int, column: int, indent: int) -> int:
    return cell_len(expand_tabs_inline(document[row][:column], indent))


def _ref_clamp_visitable(document: DocumentBase, location: tuple[int, int]) -> tuple[int, int]:
    row, column = location
    try:
        line_text = document[row]
    except IndexError:
        line_text = ""
    row = clamp(row, 0, document.line_count - 1)
    column = clamp(column, 0, len(line_text))
    return row, column


def _texts() -> list[str]:
    """Every string of up to four characters from the alphabet, alone and as the second row of a two-row text."""
    singles = ["".join(chars) for size in range(5) for chars in itertools.product(_ALPHABET, repeat=size)]
    return [*singles, *(f"ab\n{single}" for single in singles[::7])]


def test_word_locators_match_original_logic() -> None:
    """Word locators equal the stock bodies on every cursor position of random rows."""
    for text in _texts():
        widget = NovaTextArea(text)
        document = widget.document
        for row in range(document.line_count):
            for column in range(len(document[row]) + 1):
                widget.selection = Selection((row, column), (row, column))
                assert widget.get_cursor_word_left_location() == _ref_word_left(document, (row, column)), (text, row, column)
                assert widget.get_cursor_word_right_location() == _ref_word_right(document, (row, column)), (text, row, column)


def test_column_width_mapping_matches_original_logic() -> None:
    """`cell_width_to_column_index` and `get_column_width` equal the stock bodies."""
    for text in _texts():
        widget = NovaTextArea(text)
        document = widget.document
        indent = widget.indent_width
        for row in range(document.line_count):
            for column in range(len(document[row]) + 1):
                assert widget.get_column_width(row, column) == _ref_column_width(document, row, column, indent), (text, row, column)
            for cell_width in range(-1, 80):
                expected = _ref_cell_width_to_column_index(document, cell_width, row, indent)
                assert widget.cell_width_to_column_index(cell_width, row) == expected, (text, row, cell_width)


def test_clamp_visitable_matches_original_logic() -> None:
    """`clamp_visitable` equals the stock body including out-of-range rows and columns."""
    for text in _texts():
        widget = NovaTextArea(text)
        document = widget.document
        for row in range(document.line_count + 2):
            for column in range(-2, 30):
                assert widget.clamp_visitable((row, column)) == _ref_clamp_visitable(document, (row, column)), (text, row, column)


def test_selection_and_end_of_line_helpers() -> None:
    """`select_line`, `select_all` and `cursor_at_end_of_line` keep their stock results."""
    widget = NovaTextArea("abc\n\nxy z")
    widget.select_line(2)
    assert widget.selection.start == (2, 0)
    assert widget.selection.end == (2, 4)
    widget.select_line(9)
    assert widget.selection.end == (2, 4)
    widget.select_all()
    assert widget.selection.start == (0, 0)
    assert widget.selection.end == (2, 4)
    for location, expected in [((0, 3), True), ((0, 2), False), ((1, 0), True), ((2, 4), True)]:
        widget.selection = Selection(location, location)
        assert widget.cursor_at_end_of_line is expected, location


def test_word_locators_stop_at_window_edge() -> None:
    """A word longer than the 8192 character window ends at the window edge."""
    window = 8192
    widget = NovaTextArea("x" * (3 * window))
    widget.selection = Selection((0, 3 * window), (0, 3 * window))
    assert widget.get_cursor_word_left_location() == (0, 3 * window - window)
    widget.selection = Selection((0, 0), (0, 0))
    assert widget.get_cursor_word_right_location() == (0, window)
