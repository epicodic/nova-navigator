"""The navigator reads rows through capability methods, and behaves like the original on stock documents."""

from __future__ import annotations

from bisect import bisect, bisect_left, bisect_right
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from textual._cells import cell_len
from textual.geometry import clamp

from nova_editor.document import _document_navigator
from nova_editor.document._document import Document, Location
from nova_editor.document._document_navigator import DocumentNavigator, index
from nova_editor.document._wrapped_document import WrappedDocument


def test_navigator_has_no_direct_whole_row_reads() -> None:
    source = Path(_document_navigator.__file__).read_text()
    assert "len(self._document[" not in source
    assert "len(self._document.get_line(" not in source
    assert "self._document[row]" not in source
    assert "document[" not in source
    assert ".get_line(" not in source


class ReferenceNavigator:
    """Copies of the original (BASE 7840cbe) method bodies, reading whole rows directly."""

    def __init__(self, wrapped_document: WrappedDocument) -> None:
        self._wrapped_document = wrapped_document
        self._document = wrapped_document.document
        self.last_x_offset = 0

    def is_first_document_line(self, location: Location) -> bool:
        return location[0] == 0

    def is_last_document_line(self, location: Location) -> bool:
        return location[0] == self._document.line_count - 1

    def is_end_of_document_line(self, location: Location) -> bool:
        row, column = location
        row_length = len(self._document[row])
        return column == row_length

    def is_first_wrapped_line(self, location: Location) -> bool:
        if not self.is_first_document_line(location):
            return False
        row, column = location
        wrap_offsets = self._wrapped_document.get_offsets(row)
        if not wrap_offsets:
            return True
        return column < wrap_offsets[0]

    def is_last_wrapped_line(self, location: Location) -> bool:
        if not self.is_last_document_line(location):
            return False
        row, column = location
        wrap_offsets = self._wrapped_document.get_offsets(row)
        if not wrap_offsets:
            return True
        return column >= wrap_offsets[-1]

    def get_location_left(self, location: Location) -> Location:
        if location == (0, 0):
            return 0, 0
        row, column = location
        length_of_row_above = len(self._document[row - 1])
        target_row = row if column != 0 else row - 1
        target_column = column - 1 if column != 0 else length_of_row_above
        return target_row, target_column

    def get_location_above(self, location: Location) -> Location:
        line_index, column_index = location
        wrap_offsets = self._wrapped_document.get_offsets(line_index)
        section_start_columns = [0, *wrap_offsets]
        section_index = bisect_right(wrap_offsets, column_index)
        offset_within_section = column_index - section_start_columns[section_index]
        wrapped_line = self._wrapped_document.get_sections(line_index)
        section = wrapped_line[section_index]
        current_visual_offset = cell_len(section[:offset_within_section])
        target_offset = max(current_visual_offset, self.last_x_offset)
        if section_index == 0:
            if self.is_first_wrapped_line(location):
                return 0, 0
            target_row = line_index - 1
            target_column = self._wrapped_document.get_target_document_column(target_row, target_offset, -1)
            return target_row, target_column
        target_column = self._wrapped_document.get_target_document_column(line_index, target_offset, section_index - 1)
        return line_index, target_column

    def get_location_below(self, location: Location) -> Location:
        line_index, column_index = location
        document = self._document
        wrap_offsets = self._wrapped_document.get_offsets(line_index)
        section_start_columns = [0, *wrap_offsets]
        section_index = bisect(wrap_offsets, column_index)
        offset_within_section = column_index - section_start_columns[section_index]
        wrapped_line = self._wrapped_document.get_sections(line_index)
        section = wrapped_line[section_index]
        current_visual_offset = cell_len(section[:offset_within_section])
        target_offset = max(current_visual_offset, self.last_x_offset)
        if section_index == len(wrapped_line) - 1:
            if self.is_last_document_line(location):
                return line_index, len(document[line_index])
            target_row = line_index + 1
            target_column = self._wrapped_document.get_target_document_column(target_row, target_offset, 0)
            return target_row, target_column
        target_column = self._wrapped_document.get_target_document_column(line_index, target_offset, section_index + 1)
        return line_index, target_column

    def get_location_end(self, location: Location) -> Location:
        line_index, column_offset = location
        wrap_offsets = self._wrapped_document.get_offsets(line_index)
        if wrap_offsets:
            next_offset_right = bisect(wrap_offsets, column_offset)
            if next_offset_right == len(wrap_offsets):
                return line_index, len(self._document[line_index])
            return line_index, wrap_offsets[next_offset_right] - 1
        target_column = len(self._document[line_index])
        return line_index, target_column

    def get_location_home(self, location: Location, smart_home: bool = False) -> Location:
        line_index, column_offset = location
        wrap_offsets = self._wrapped_document.get_offsets(line_index)
        if wrap_offsets:
            next_offset_left = bisect(wrap_offsets, column_offset)
            if next_offset_left == 0:
                return line_index, 0
            return line_index, wrap_offsets[next_offset_left - 1]
        line = self._wrapped_document.document[line_index]
        target_column = 0
        if smart_home:
            for code_point_index, code_point in enumerate(line):
                if not code_point.isspace():
                    target_column = code_point_index
                    break
            if column_offset == 0 or column_offset > target_column:
                return line_index, target_column
        return line_index, 0

    def clamp_reachable(self, location: Location) -> Location:
        document = self._document
        row, column = location
        clamped_row = clamp(row, 0, document.line_count - 1)
        row_text = self._document[clamped_row]
        clamped_column = clamp(column, 0, len(row_text))
        return clamped_row, clamped_column


def reference_index(sequence: Sequence[int], value: int) -> int:
    insert_index = bisect_left(sequence, value)
    if insert_index != len(sequence) and sequence[insert_index] == value:
        return insert_index
    return -1


def _make_text() -> str:
    state = 7
    alphabet = ["a", "b", " ", "\t", "日", "é", "x", "y", "  "]
    rows = ["", "short", "\tindented\ttab", "   leading spaces here", "日本語日本語日本語日本語日本語", "a" * 47, "word " * 12, ""]
    for _ in range(12):
        chars: list[str] = []
        state = (state * 1103515245 + 12345) % 2**31
        for _ in range(state % 46):
            state = (state * 1103515245 + 12345) % 2**31
            chars.append(alphabet[(state >> 8) % len(alphabet)])
        rows.append("".join(chars))
    rows.append("supercalifragilisticexpialidocious_and_more_long_words_without_spaces")
    rows.append("last row")
    return "\n".join(rows)


def _outcome(function: Callable[[], Any]) -> Any:
    try:
        return function()
    except (IndexError, ValueError, TypeError, KeyError) as error:
        return type(error)


@pytest.mark.parametrize("width", [10, 20, 0])
@pytest.mark.parametrize("trailing_newline", [False, True])
def test_navigator_matches_original_on_every_location(width: int, trailing_newline: bool) -> None:
    text = _make_text() + ("\n" if trailing_newline else "")
    document = Document(text)
    wrapped = WrappedDocument(document, width=width, tab_width=4)
    wrapped.wrap(width, tab_width=4)
    new = DocumentNavigator(wrapped)
    ref = ReferenceNavigator(wrapped)

    names = [
        "is_end_of_document_line",
        "is_first_wrapped_line",
        "is_last_wrapped_line",
        "get_location_left",
        "get_location_above",
        "get_location_below",
        "get_location_end",
        "get_location_home",
        "clamp_reachable",
    ]
    checked = 0
    for last_x in (0, 9):
        new.last_x_offset = ref.last_x_offset = last_x
        for row in range(document.line_count):
            for column in range(len(document[row]) + 2):
                location = (row, column)
                for name in names:
                    got = _outcome(lambda name=name, location=location: getattr(new, name)(location))
                    want = _outcome(lambda name=name, location=location: getattr(ref, name)(location))
                    assert got == want, (name, location, width, last_x)
                    checked += 1
                for smart in (False, True):
                    got = _outcome(lambda smart=smart, location=location: new.get_location_home(location, smart))
                    want = _outcome(lambda smart=smart, location=location: ref.get_location_home(location, smart))
                    assert got == want, ("home", smart, location, width)
    for location in [(-1, 0), (-1, 5), (document.line_count, 0), (document.line_count + 3, 99), (0, -4), (2, 500)]:
        assert new.clamp_reachable(location) == ref.clamp_reachable(location)
    assert checked > 1000


def test_index_matches_original_and_accepts_lazy_sequences() -> None:
    sequence = [3, 7, 7, 12, 20]
    for value in range(25):
        assert index(sequence, value) == reference_index(sequence, value)

    class Lazy(Sequence[int]):
        def __getitem__(self, item: Any) -> Any:
            raise AssertionError("must not be indexed")

        def __len__(self) -> int:
            raise AssertionError("must not be measured")

        def index_of(self, column: int) -> int:
            return 41 if column == 5 else -1

        def bisect_right(self, column: int) -> int:
            return column

        def is_last_section(self, column: int) -> bool:
            return column > 0

    assert index(Lazy(), 5) == 41
    assert index(Lazy(), 6) == -1
