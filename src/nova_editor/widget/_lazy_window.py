"""Windowed text of one row of a lazy document, without Textual (ACT3 design 6).

A window is the part of a medium or long row that the widget renders: the visible display columns plus a margin on each side (no-wrap),
or one grid-wrapped section (wrap). The window starts at the character that covers its first display column and never decodes more than
`MAX_WINDOW_CHARS` characters. The widget prepends `phantom_cells` blank cells before expanding tabs so that tab stops keep their phase
even though the row is decoded from the middle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol

from nova_editor.document._lazy_document import MAX_WINDOW_CHARS

if TYPE_CHECKING:
    from nova_editor.document._lazy_wrapped_document import LazyWrappedDocument


class WindowSource(Protocol):
    """The capability methods of `LazyDocument` that a window needs."""

    def line_length(self, row: int) -> int | None:
        """Return the character count of the row, or None while it is not known."""
        ...

    def column_slice(self, row: int, start: int, stop: int) -> str:
        """Return characters [start, stop) of the row, never beyond the scanned frontier."""
        ...

    def display_column(self, row: int, column: int, tab_width: int = 4) -> int | None:
        """Return the display column of a character column, or None beyond the frontier."""
        ...

    def column_at_display(self, row: int, x: int, tab_width: int = 4) -> int | None:
        """Return the character column covering display column `x`, or None beyond the frontier."""
        ...


class WindowText(NamedTuple):
    """Decoded window of a row.

    `text` starts at character column `start_column`, whose display column is `start_disp`; `phantom_cells` (`start_disp % tab_width`)
    blank cells must be placed before it when expanding tabs. `at_row_end` means the text reaches the end of a completely scanned row;
    `truncated` means the scanned frontier (or the decode bound) ends the text before the requested window does; `missing` means the
    window start itself is not scanned yet and `text` is empty.
    """

    text: str
    start_column: int
    start_disp: int
    phantom_cells: int
    at_row_end: bool
    truncated: bool
    missing: bool


def _missing() -> WindowText:
    return WindowText("", 0, 0, 0, at_row_end=False, truncated=True, missing=True)


def _finish(document: WindowSource, row: int, start: int, start_disp: int, stop: int, *, stop_known: bool, tab_width: int) -> WindowText:
    """Decode [start, stop) and classify the result; `stop_known` is False when `stop` is only an upper bound beyond the frontier."""
    stop = min(stop, start + MAX_WINDOW_CHARS)
    text = document.column_slice(row, start, stop)
    total = document.line_length(row)
    at_end = total is not None and start + len(text) >= total
    truncated = not at_end and (not stop_known or len(text) < stop - start)
    return WindowText(text, start, start_disp, start_disp % tab_width, at_row_end=at_end, truncated=truncated, missing=False)


def window_text(document: WindowSource, row: int, first_col_disp: int, width: int, margin: int, tab_width: int = 4) -> WindowText:
    """Return the text covering display columns [first_col_disp - margin, first_col_disp + width + margin) of a row.

    Args:
        document: The lazy document.
        row: The row (medium or long; short rows work too).
        first_col_disp: First visible display column.
        width: Visible width in cells.
        margin: Extra cells decoded on each side.
        tab_width: Tab stop width used for medium rows (long rows use the document configuration).

    Returns:
        The window; see `WindowText`.
    """
    low = max(0, first_col_disp - margin)
    high = max(low + 1, first_col_disp + width + margin)
    start = document.column_at_display(row, low, tab_width)
    if start is None:
        return _missing()
    start_disp = document.display_column(row, start, tab_width)
    if start_disp is None:
        return _missing()
    last = document.column_at_display(row, high - 1, tab_width)
    if last is None:
        return _finish(document, row, start, start_disp, start + MAX_WINDOW_CHARS, stop_known=False, tab_width=tab_width)
    return _finish(document, row, start, start_disp, last + 1, stop_known=True, tab_width=tab_width)


def section_window(document: WindowSource, wrapped: LazyWrappedDocument, row: int, section: int, tab_width: int = 4) -> WindowText:
    """Return the text of one grid-wrapped section of a row (the section's first character is at cell 0 of the strip).

    Args:
        document: The lazy document.
        wrapped: Its wrapped view, which knows where sections start.
        row: The row.
        section: The section index.
        tab_width: Tab stop width used for medium rows.

    Returns:
        The window; see `WindowText`.
    """
    start = wrapped.section_start(row, section)
    if start is None:
        return _missing()
    start_disp = document.display_column(row, start, tab_width) if start else 0
    if start_disp is None:
        return _missing()
    following = wrapped.section_start(row, section + 1)
    if following is None:
        return _finish(document, row, start, start_disp, start + MAX_WINDOW_CHARS, stop_known=False, tab_width=tab_width)
    return _finish(document, row, start, start_disp, following, stop_known=True, tab_width=tab_width)
