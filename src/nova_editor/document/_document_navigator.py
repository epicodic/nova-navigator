import re
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from rich.cells import cell_len
from textual.geometry import Offset, clamp

from nova_editor.document._document import Location
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.document._lazy_wrapped_document import LazyWrappedDocument
from nova_editor.document._wrapped_document import WrappedDocument

_SMART_HOME_WINDOW = 8192
"""Characters scanned for smart home when the row length is not known."""


@runtime_checkable
class LazyOffsets(Protocol):
    """Wrap offsets of a row that are computed lazily instead of being held in a list."""

    def bisect_right(self, column: int) -> int:
        """Return the number of wrap offsets that are less than or equal to `column`."""
        ...

    def is_last_section(self, column: int) -> bool:
        """Return whether `column` lies in the last section of the row."""
        ...

    def index_of(self, column: int) -> int:
        """Return the index of the wrap offset equal to `column`, or -1."""
        ...


class DocumentNavigator:
    """Cursor navigation in the TextArea is "wrapping-aware".

    Although the cursor location (the selection) is represented as a location
    in the raw document, when you actually *move* the cursor, it must take wrapping
    into account (otherwise things start to look really confusing to the user where
    wrapping is involved).

    Your cursor visually moves through the wrapped version of the document, rather
    than the raw document. So, for example, pressing down on the keyboard
    may move your cursor to a position further along the current raw document line,
    rather than on to the next line in the raw document.

    The DocumentNavigator class manages that behavior.

    Given a cursor location in the unwrapped document, and a cursor movement action,
    this class can inform us of the destination the cursor will move to considering
    the current wrapping width and document content. It can also translate between
    document-space (a location/(row,col) in the raw document), and visual-space
    (x and y offsets) as the user will see them on screen after the document has been
    wrapped.

    For this to work correctly, the wrapped_document and document must be synchronised.
    This means that if you make an edit to the document, you *must* then update the
    wrapped document, and *then* you may query the document navigator.

    Naming conventions:

    A "location" refers to a location, in document-space (in the raw document). It
    is entirely unrelated to visually positioning. A location in a document can appear
    in any visual position, as it is influenced by scrolling, wrapping, gutter settings,
    and the cell width of characters to its left.

    A "wrapped section" refers to a portion of the line accounting for wrapping.
    For example the line "ABCDEF" when wrapped at width 3 will result in 2 sections:
    "ABC" and "DEF". In this case, we call "ABC" is the first section/wrapped section.

    A "wrap offset" is an integer representing the index at which wrapping occurs in a
    document-space line. This is a codepoint index, rather than a visual offset.
    In "ABCDEF" with wrapping at width 3, there is a single wrap offset of 3.

    "Smart home" refers to a modification of the "home" key behavior. If smart home is
    enabled, the first non-whitespace character is considered to be the home location.
    If the cursor is currently at this position, then the normal home behavior applies.
    This is designed to make cursor movement more useful to end users.
    """

    def __init__(self, wrapped_document: WrappedDocument) -> None:
        """Create a DocumentNavigator.

        Args:
            wrapped_document: The WrappedDocument to be used when making navigation decisions.
        """
        self._wrapped_document = wrapped_document
        self._document = wrapped_document.document

        self._word_pattern = re.compile(r"(?<=\W)(?=\w)|(?<=\w)(?=\W)")
        """Compiled regular expression for what we consider to be a 'word'."""

        self.last_x_offset = 0
        """Remembers the last x offset (cell width) the cursor was moved horizontally to,
        so that it can be restored on vertical movement where possible."""

    def _line_length(self, row: int, fallback: int) -> int:
        """Return the character count of a row, or `fallback` when the length is not yet known.

        Args:
            row: The document row.
            fallback: The value to use when the document cannot report the length yet.

        Returns:
            The length of the row, or `fallback`.
        """
        length = self._document.line_length(row)
        return fallback if length is None else length

    def _long_wrapped(self, row: int) -> LazyWrappedDocument | None:
        """Return the lazy wrapped document when `row` is a long row (never decoded as a whole), else `None`."""
        wrapped = self._wrapped_document
        if isinstance(wrapped, LazyWrappedDocument) and self._document.is_long(row):
            return wrapped
        return None

    def _known_or_estimated_length(self, row: int) -> int:
        """Return the exact length of a row, or for an unscanned long row the immediate estimate (never waits)."""
        length = self._document.line_length(row)
        if length is not None:
            return length
        document = self._document
        return document.long_index(row).estimate_length() if isinstance(document, LazyDocument) else 0

    def end_is_known(self, row: int) -> bool:
        """Return whether the length of `row` is known, so that end-of-row results are exact.

        Always true for short and medium rows. False for a long row whose scan has not completed: `get_location_end`
        then returns the location it was given (the sentinel "end not known yet") and the caller must jump to the byte anchor
        of the row end instead of using a column.
        """
        return self._document.line_length(row) is not None

    def is_start_of_document_line(self, location: Location) -> bool:
        """True when the location is at the start of the first document line.

        Args:
            location: The location to check.

        Returns:
             True if the location is at column index 0.
        """
        return location[1] == 0

    def is_start_of_wrapped_line(self, location: Location) -> bool:
        """True when the location is at the start of the first wrapped line.

        Args:
            location: The location to check.

        Returns:
             True if the location is at column index 0.
        """
        if self.is_start_of_document_line(location):
            return True

        row, column = location
        long_wrapped = self._long_wrapped(row)
        if long_wrapped is not None:
            return self._is_section_start(long_wrapped, row, column)
        wrap_offsets = self._wrapped_document.get_offsets(row)
        return index(wrap_offsets, column) != -1

    @staticmethod
    def _is_section_start(wrapped: LazyWrappedDocument, row: int, column: int) -> bool:
        """Return whether `column` is the first column of a wrapped section of a long row (never true without wrapping)."""
        if column <= 0:
            return False
        section = wrapped.section_index(row, column)
        return section >= 1 and wrapped.section_start(row, section) == column

    def is_end_of_document_line(self, location: Location) -> bool:
        """True if the location is at the end of a line in the document.

        Note that the "end" of a line is equal to its length (one greater
        than the final index), since there is a space at the end of the line
        for the cursor to rest.

        Args:
            location: The location to examine.

        Returns:
            True if and only if the document is at the end of a line in the document.
        """
        row, column = location
        row_length = self._document.line_length(row)
        if row_length is None:
            if self._document.is_long(row):
                return False  # the length of an unscanned long row is unknown: its end has not been seen
            return not self._document.has_char_at(row, column)
        return column == row_length

    def is_end_of_wrapped_line(self, location: Location) -> bool:
        """True if the location is at the end of a wrapped line.

        Args:
            location: The location to examine.

        Returns:
            True if and only if the cursor is on the last wrapped section of *any* line.
        """
        if self.is_end_of_document_line(location):
            return True

        row, column = location
        long_wrapped = self._long_wrapped(row)
        if long_wrapped is not None:
            return self._is_section_start(long_wrapped, row, column - 1)
        wrap_offsets = self._wrapped_document.get_offsets(row)
        return index(wrap_offsets, column - 1) != -1

    def is_first_document_line(self, location: Location) -> bool:
        """Check if the given location is on the first line in the document.

        Args:
            location: The location to examine.

        Returns:
            True if and only if the cursor is on the first line of the document.
        """
        return location[0] == 0

    def is_first_wrapped_line(self, location: Location) -> bool:
        """Check if the given location is on the first wrapped section of the first line in the document.

        Args:
            location: The location to examine.

        Returns:
            True if and only if the cursor is on the first wrapped section of the first line.
        """
        if not self.is_first_document_line(location):
            return False

        row, column = location
        long_wrapped = self._long_wrapped(row)
        if long_wrapped is not None:
            return long_wrapped.section_index(row, column) == 0
        wrap_offsets = self._wrapped_document.get_offsets(row)

        if not wrap_offsets:
            return True

        return _bisect_right(wrap_offsets, column) == 0

    def is_last_document_line(self, location: Location) -> bool:
        """Check if the given location is on the last line of the document.

        Args:
            location: The location to examine.

        Returns:
            True when the location is on the last line of the document.
        """
        return location[0] == self._document.line_count - 1

    def is_last_wrapped_line(self, location: Location) -> bool:
        """Check if the given location is on the last wrapped section of the last line.

        That is, the cursor is *visually* on the last rendered row.

        Args:
            location: The location to examine.

        Returns:
            True if and only if the cursor is on the last section of the last line.
        """
        if not self.is_last_document_line(location):
            return False

        row, column = location
        long_wrapped = self._long_wrapped(row)
        if long_wrapped is not None:
            return not long_wrapped.wrap_width or long_wrapped.is_last_section(row, column)
        wrap_offsets = self._wrapped_document.get_offsets(row)

        if not wrap_offsets:
            return True

        if isinstance(wrap_offsets, LazyOffsets):
            return wrap_offsets.is_last_section(column)
        return column >= wrap_offsets[-1]

    def is_start_of_document(self, location: Location) -> bool:
        """Check if a location is at the start of the document.

        Args:
            location: The location to examine.

        Returns:
            True if and only if the cursor is at document location (0, 0)
        """
        return location == (0, 0)

    def is_end_of_document(self, location: Location) -> bool:
        """Check if a location is at the end of the document.

        Args:
            location: The location to examine.

        Returns:
            True if and only if the cursor is at the end of the document.
        """
        return self.is_last_document_line(location) and self.is_end_of_document_line(location)

    def get_location_left(self, location: Location) -> Location:
        """Get the location to the left of the given location.

        Note that if the given location is at the start of the line, then
        this will return the end of the preceding line, since that's where
        you would expect the cursor to move.

        Args:
            location: The location to start from.

        Returns:
            The location to the right.
        """
        if location == (0, 0):
            return 0, 0

        row, column = location
        if column != 0:
            return row, column - 1
        if self._document.is_long(row - 1):
            return row - 1, self._known_or_estimated_length(row - 1)
        return row - 1, self._line_length(row - 1, 0)

    def get_location_right(self, location: Location) -> Location:
        """Get the location to the right of the given location.

        Note that if the given location is at the end of the line, then
        this will return the start of the following line, since that's where
        you would expect the cursor to move.

        Args:
            location: The location to start from.

        Returns:
            The location to the right.
        """
        if self.is_end_of_document(location):
            return location
        row, column = location
        is_end_of_line = self.is_end_of_document_line(location)
        target_row = row + 1 if is_end_of_line else row
        target_column = 0 if is_end_of_line else column + 1
        return target_row, target_column

    def get_location_above(self, location: Location) -> Location:
        """Get the location visually aligned with the cell above the given location.

        Args:
            location: The location to start from.

        Returns:
            The cell above the given location.
        """
        # Get the wrap offsets of the current line.
        line_index, column_index = location
        long_wrapped = self._long_wrapped(line_index)
        if long_wrapped is not None:
            section, x = long_wrapped.section_x(line_index, column_index)
            target_offset = max(x, self.last_x_offset)
            if section == 0:
                if self.is_first_wrapped_line(location):
                    return 0, 0
                target_row = line_index - 1
                return target_row, long_wrapped.get_target_document_column(target_row, target_offset, -1)
            return line_index, long_wrapped.get_target_document_column(line_index, target_offset, section - 1)
        wrap_offsets = self._wrapped_document.get_offsets(line_index)
        section_start_columns = [0, *wrap_offsets]

        # We need to find the insertion point to determine which section index we're
        # on within the current line. When we know the section index, we can use it
        # to find the section which sits above it.
        section_index = _bisect_right(wrap_offsets, column_index)
        offset_within_section = column_index - section_start_columns[section_index]
        wrapped_line = self._wrapped_document.get_sections(line_index)
        section = wrapped_line[section_index]

        # Convert that cursor offset to a cell (visual) offset
        current_visual_offset = cell_len(section[:offset_within_section])
        target_offset = max(current_visual_offset, self.last_x_offset)

        if section_index == 0:
            # Moving up from a position on the first visual line moves us to the start.
            if self.is_first_wrapped_line(location):
                return 0, 0
            # Get the last section from the line above, and find where to move in it.
            target_row = line_index - 1
            target_column = self._wrapped_document.get_target_document_column(target_row, target_offset, -1)
            target_location = target_row, target_column
        else:
            # Stay on the same document line, but move backwards.
            # Since the section above could be shorter, we need to clamp the column
            # to a valid value.
            target_column = self._wrapped_document.get_target_document_column(line_index, target_offset, section_index - 1)
            target_location = line_index, target_column

        return target_location

    def get_location_below(self, location: Location) -> Location:
        """Given a location in the raw document, return the raw document
        location corresponding to moving down in the wrapped representation
        of the document.

        Args:
            location: The location in the raw document.

        Returns:
            The location which is *visually* below the given location.
        """
        line_index, column_index = location
        long_wrapped = self._long_wrapped(line_index)
        if long_wrapped is not None:
            section, x = long_wrapped.section_x(line_index, column_index)
            target_offset = max(x, self.last_x_offset)
            if not long_wrapped.wrap_width or long_wrapped.is_last_section(line_index, column_index):
                if self.is_last_document_line(location):
                    return line_index, self._line_length(line_index, column_index)
                target_row = line_index + 1
                return target_row, long_wrapped.get_target_document_column(target_row, target_offset, 0)
            return line_index, long_wrapped.get_target_document_column(line_index, target_offset, section + 1)
        wrap_offsets = self._wrapped_document.get_offsets(line_index)
        section_start_columns = [0, *wrap_offsets]
        section_index = _bisect_right(wrap_offsets, column_index)
        offset_within_section = column_index - section_start_columns[section_index]
        wrapped_line = self._wrapped_document.get_sections(line_index)
        section = wrapped_line[section_index]
        current_visual_offset = cell_len(section[:offset_within_section])
        target_offset = max(current_visual_offset, self.last_x_offset)

        # If we're at the last section/row of a wrapped line
        if section_index == len(wrapped_line) - 1:
            # Last section of last line: go to end of file.
            if self.is_last_document_line(location):
                return line_index, self._line_length(line_index, column_index)

            # Go to the first section of the line below.
            target_row = line_index + 1
            target_column = self._wrapped_document.get_target_document_column(target_row, target_offset, 0)
            target_location = target_row, target_column
        else:
            # Stay on the same document line, but move forwards to
            # the location on the section below with the same visual offset.
            target_column = self._wrapped_document.get_target_document_column(line_index, target_offset, section_index + 1)
            target_location = line_index, target_column

        return target_location

    def get_location_end(self, location: Location) -> Location:
        """Get the location corresponding to the end of the current section.

        Args:
            location: The current location.

        Returns:
            The location corresponding to the end of the wrapped line.
        """
        line_index, column_offset = location
        long_wrapped = self._long_wrapped(line_index)
        if long_wrapped is not None:
            if long_wrapped.wrap_width:
                following = long_wrapped.section_start(line_index, long_wrapped.section_index(line_index, column_offset) + 1)
                if following is not None:
                    return line_index, following - 1
            # Last section, or the next section start is not scanned yet: the row end; the location itself when that is unknown too.
            return line_index, self._line_length(line_index, column_offset)
        wrap_offsets = self._wrapped_document.get_offsets(line_index)
        if wrap_offsets:
            # Get the next wrap offset to the right
            next_offset_right = _bisect_right(wrap_offsets, column_offset)
            # There's no more wrapping to the right of this location - go to line end.
            if next_offset_right == len(wrap_offsets):
                return line_index, self._line_length(line_index, column_offset)
            # We've found a wrap point
            return line_index, wrap_offsets[next_offset_right] - 1
        else:
            # No wrapping to consider - go to the start/end of the document line.
            target_column = self._line_length(line_index, column_offset)
            return line_index, target_column

    def get_location_home(self, location: Location, smart_home: bool = False) -> Location:
        """Get the "home location" corresponding to the given location.

        Args:
            location: The location to consider.
            smart_home: Enable/disable 'smart home' behavior.

        Returns:
            The home location, relative to the given location.
        """
        line_index, column_offset = location
        long_wrapped = self._long_wrapped(line_index)
        if long_wrapped is not None:
            if long_wrapped.wrap_width:
                start = long_wrapped.section_start(line_index, long_wrapped.section_index(line_index, column_offset))
                return line_index, column_offset if start is None else start
            if smart_home:
                window = self._document.column_slice(line_index, 0, _SMART_HOME_WINDOW)
                first = next((position for position, code_point in enumerate(window) if not code_point.isspace()), 0)
                if column_offset == 0 or column_offset > first:
                    return line_index, first
            return line_index, 0
        wrap_offsets = self._wrapped_document.get_offsets(line_index)
        if wrap_offsets:
            next_offset_left = _bisect_right(wrap_offsets, column_offset)
            if next_offset_left == 0:
                return line_index, 0
            return line_index, wrap_offsets[next_offset_left - 1]
        else:
            # No wrapping to consider, go to the start of the document line
            target_column = 0
            if smart_home:
                line = self._document.column_slice(line_index, 0, self._line_length(line_index, _SMART_HOME_WINDOW))
                for code_point_index, code_point in enumerate(line):
                    if not code_point.isspace():
                        target_column = code_point_index
                        break

                if column_offset == 0 or column_offset > target_column:
                    return line_index, target_column

            return line_index, 0

    def get_location_at_y_offset(self, location: Location, vertical_offset: int) -> Location:
        """Apply a visual vertical offset to a location and check the resulting location.

        Args:
            location: The location to start from.
            vertical_offset: The vertical offset to move (negative=up, positive=down).

        Returns:
            The location after the offset has been applied.
        """
        long_wrapped = self._long_wrapped(location[0])
        if long_wrapped is not None:
            row = location[0]
            section, x = long_wrapped.section_x(row, location[1])
            target_section = section + vertical_offset
            # The cursor may lie past the estimated section count of an unscanned row: it still counts as one section of the row.
            if long_wrapped.wrap_width and 0 <= target_section < max(long_wrapped.row_sections(row), section + 1):
                return row, long_wrapped.get_target_document_column(row, x, target_section)
            # Leaving the row: the estimated vertical space of an unscanned row can disagree with the cursor, so clamp.
            x_offset, y_offset = long_wrapped.location_to_offset(location)
            return self.clamp_reachable(long_wrapped.offset_to_location(Offset(x_offset, y_offset + vertical_offset)))
        # Convert into offset-space to apply the offset.
        x_offset, y_offset = self._wrapped_document.location_to_offset(location)
        # Convert the offset with the delta applied back to location-space.
        return self._wrapped_document.offset_to_location(
            Offset(x_offset, y_offset + vertical_offset),
        )

    def clamp_reachable(self, location: Location) -> Location:
        """Given a location, return the nearest location that corresponds to a
        reachable location in the document.

        Args:
            location: A location.

        Returns:
            The nearest reachable location in the document.
        """
        document = self._document
        row, column = location
        clamped_row = clamp(row, 0, document.line_count - 1)

        clamped_column = clamp(column, 0, self._line_length(clamped_row, column))
        return clamped_row, clamped_column


def _bisect_right(sequence: Any, value: int) -> int:
    """Return `bisect_right` for a stock list or a lazy offset object exposing `bisect_right`."""
    if isinstance(sequence, LazyOffsets):
        return sequence.bisect_right(value)
    return bisect_right(sequence, value)


def index(sequence: Sequence, value: Any) -> int:
    """Locate the leftmost item in the sequence equal to value via bisection.

    Args:
        sequence: The sequence to search in.
        value: The value to find.

    Returns:
        The index of the value, or -1 if the value is not found in the sequence.
    """
    if isinstance(sequence, LazyOffsets):
        return sequence.index_of(value)
    insert_index = bisect_left(sequence, value)
    if insert_index != len(sequence) and sequence[insert_index] == value:
        return insert_index
    return -1
