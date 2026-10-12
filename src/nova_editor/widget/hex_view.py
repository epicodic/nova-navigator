"""The hex view: rows of offset, hex digits and characters over a `LazyDocument` (S0003 ACT4).

The view reads only `document.length`, `document.revision` and `document.read_bytes` (REQ-21). One byte window of
three screens of rows around the first visible row is its whole cache: the first page is one read, a scroll inside
the window none, any jump (including the end of an arbitrarily large file) one read of at most three screens.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Literal

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip

from nova_editor.core import SourceChanged
from nova_editor.document import LazyDocument, RowUnavailable
from nova_editor.widget.hex_layout import HexLayout, Pane, choose_layout, format_row, total_rows

_WINDOW_SCREENS_BEFORE = 1
"""Screens of rows the window holds above the first visible row."""
_WINDOW_SCREENS_AFTER = 2
"""Screens of rows the window holds from the first visible row on (the visible screen and the next)."""

_WindowKey = tuple[LazyDocument, int, int]
"""What a window is valid for: the document, its revision and the bytes per row."""


@dataclass(frozen=True)
class HexCursor:
    """Position of the hex view cursor."""

    offset: int
    """Byte offset in `[0, length]`; `length` is the end-of-document position."""
    nibble: Literal[0, 1]
    """0 for the high digit, 1 for the low digit; always 0 in the character pane and at the end of the document."""
    pane: Pane
    """The pane the cursor is in."""


@dataclass(frozen=True)
class ByteSelection:
    """A selection of whole bytes, half-open `[start, end)` with `start < end`."""

    start: int
    """First selected byte."""
    end: int
    """One past the last selected byte."""


class HexView(ScrollView):
    """Rows of offset column, hex pane and character pane over the document the provider returns.

    The view never owns the document: the provider (the text widget's `document`) decides which one is shown, and a
    replaced document (reload) is picked up by identity, a changed one by its revision.
    """

    DEFAULT_CSS: ClassVar[str] = """\
HexView {
    width: 1fr;
    height: 1fr;
    color: $foreground;
    background: $surface;
    overflow-x: hidden;
    overflow-y: scroll;
    & .hex-view--offset {
        color: $foreground 50%;
    }
    & .hex-view--separator {
        color: $foreground 40%;
    }
    & .hex-view--mark {
        background: $foreground 30%;
    }
    & .hex-view--selection {
        background: $input-selection-background;
        color: $input-selection-foreground;
    }
    &:ansi {
        .hex-view--cursor {
            color: $input-cursor-foreground;
            background: $input-cursor-background;
            text-style: reverse;
        }
        & .hex-view--selection {
            background: transparent;
            text-style: reverse;
        }
    }
    &:dark {
        .hex-view--cursor {
            color: $input-cursor-foreground;
            background: $input-cursor-background;
        }
    }
    &:light {
        .hex-view--cursor {
            color: $text 90%;
            background: $foreground 70%;
        }
    }
}
"""
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "hex-view--offset",
        "hex-view--separator",
        "hex-view--cursor",
        "hex-view--mark",
        "hex-view--selection",
    }

    class Moved(Message):
        """The cursor or the selection changed."""

        def __init__(self, cursor: HexCursor, selection: ByteSelection | None) -> None:
            super().__init__()
            self.cursor = cursor
            """The cursor after the change."""
            self.selection = selection
            """The selection after the change."""

    def __init__(self, document: Callable[[], LazyDocument], *, id: str | None = None) -> None:
        """Create the view.

        Args:
            document: Returns the document to show; called on every refresh, so a replaced document is followed.
            id: The widget id.
        """
        super().__init__(id=id)
        self._document_provider = document
        self.cursor = HexCursor(0, 0, "hex")
        """The cursor."""
        self.insert_mode = False
        """Insert (True) or overwrite (False) for typing; typing is ACT5."""
        self.on_source_changed: Callable[[SourceChanged], None] | None = None
        """Called once per document when a read reports that the file changed."""
        self._anchor: int | None = None
        """The fixed end of the selection, or `None`."""
        self._document: LazyDocument | None = None
        """The document the view last synchronised with."""
        self._revision = -1
        """The revision of `_document` the layout and the cursor were clamped for."""
        self._length = 0
        """The length of `_document` at `_revision`."""
        self._layout = choose_layout(0, 0)
        """The row geometry for `_width` and `_length`."""
        self._width = -1
        """The content width `_layout` was chosen for."""
        self._win_key: _WindowKey | None = None
        """What the window holds bytes of, or `None` when it is empty."""
        self._win_start = 0
        """Byte offset of the first byte of the window."""
        self._win_data = b""
        """The bytes of the window rows `[_win_first_row, _win_end_row)`."""
        self._win_first_row = 0
        """First row of the window."""
        self._win_end_row = 0
        """One past the last row of the window."""
        self._failed_key: _WindowKey | None = None
        """The key a read failed for; no read is tried again until the key changes."""
        self._reported = False
        """Whether a source change of `_document` was reported."""

    # -- public state ---------------------------------------------------------------------------------------------------

    @property
    def selection(self) -> ByteSelection | None:
        """The selected bytes, or `None`; derived from the anchor and the cursor, so it always holds whole bytes."""
        anchor = self._anchor
        if anchor is None or anchor == self.cursor.offset:
            return None
        return ByteSelection(min(anchor, self.cursor.offset), max(anchor, self.cursor.offset))

    @property
    def length(self) -> int:
        """Length of the document in bytes."""
        return self._document_provider().length

    @property
    def hex_layout(self) -> HexLayout:
        """The row geometry for the present width and document length."""
        self._sync()
        return self._layout

    def follow_document(self) -> None:
        """Pick up a replaced or changed document and repaint."""
        self._sync()
        self.refresh()

    # -- synchronisation ------------------------------------------------------------------------------------------------

    def _sync(self) -> None:
        """Compare the document identity, its revision and the width with what was last drawn, and adapt."""
        document = self._document_provider()
        width = self.scrollable_content_region.width
        if document is not self._document:
            self._document = document
            self._reported = False
            self._anchor = None
            self._revision = document.revision
            self._length = document.length
            self._drop_window()
            self.cursor = HexCursor(min(self.cursor.offset, self._length), 0, self.cursor.pane)
            self._relayout(width)
        elif document.revision != self._revision:
            self._revision = document.revision
            self._length = document.length
            self._drop_window()
            self._clamp_cursor()
            self._relayout(width)
        elif width != self._width:
            self._relayout(width)

    def _clamp_cursor(self) -> None:
        """Clamp the cursor and the anchor to `[0, length]`; the end-of-document position has no low digit."""
        cursor = self.cursor
        if cursor.offset >= self._length:
            self.cursor = HexCursor(self._length, 0, cursor.pane)
        if self._anchor is not None:
            self._anchor = min(self._anchor, self._length)

    def _relayout(self, width: int) -> None:
        """Choose the layout for `width` and the length; keep the byte offset of the top row at the top."""
        old_bytes_per_row = self._layout.bytes_per_row
        top_offset = self.scroll_offset.y * old_bytes_per_row
        self._layout = choose_layout(width, self._length)
        self._width = width
        bytes_per_row = self._layout.bytes_per_row
        self.virtual_size = Size(self._layout.total_width, total_rows(self._length, bytes_per_row))
        target = top_offset // bytes_per_row
        if target != self.scroll_offset.y or target > self.max_scroll_y:
            self.scroll_to(y=target, animate=False, immediate=True)

    def _drop_window(self) -> None:
        self._win_key = None
        self._win_data = b""
        self._win_start = 0
        self._win_first_row = 0
        self._win_end_row = 0
        self._failed_key = None

    # -- the byte window ------------------------------------------------------------------------------------------------

    def _row_bytes(self, row: int) -> bytes | None:
        """Return the bytes of `row` from the window, refilling it once around the first visible row; `None` when unreadable."""
        document = self._document
        if document is None:
            return None
        bytes_per_row = self._layout.bytes_per_row
        key: _WindowKey = (document, self._revision, bytes_per_row)
        if not self._window_holds(key, row) and key != self._failed_key:
            self._refill(document, key, row)
        if not self._window_holds(key, row):
            return None
        start = (row - self._win_first_row) * bytes_per_row
        return self._win_data[start : start + bytes_per_row]

    def _window_holds(self, key: _WindowKey, row: int) -> bool:
        return key == self._win_key and self._win_first_row <= row < self._win_end_row

    def _refill(self, document: LazyDocument, key: _WindowKey, row: int) -> None:
        """Read the rows `[top - h, top + 2h)` in one `read_bytes`; keep the old window when the read fails."""
        bytes_per_row = self._layout.bytes_per_row
        height = max(1, self.scrollable_content_region.height)
        top = self.scroll_offset.y
        if not top <= row < top + height:
            top = row
        first = max(0, top - _WINDOW_SCREENS_BEFORE * height)
        end = min(total_rows(self._length, bytes_per_row), top + _WINDOW_SCREENS_AFTER * height)
        try:
            data = document.read_bytes(first * bytes_per_row, (end - first) * bytes_per_row)
        except SourceChanged as error:
            self._failed_key = key
            if not self._reported:
                self._reported = True
                if self.on_source_changed is not None:
                    self.on_source_changed(error)
            return
        except (ValueError, RowUnavailable):
            self._failed_key = key
            return
        self._win_key = key
        self._win_start = first * bytes_per_row
        self._win_data = data
        self._win_first_row = first
        self._win_end_row = end

    # -- rendering ------------------------------------------------------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        """Render the visible line `y`: one row of the document, or blank below the last row."""
        self._sync()
        width = self.scrollable_content_region.width
        base = self.rich_style
        layout = self._layout
        row = self.scroll_offset.y + y
        if row >= total_rows(self._length, layout.bytes_per_row):
            return Strip.blank(width, base)
        data = self._row_bytes(row)
        if data is None:
            return Strip.blank(width, base)
        return self._row_strip(layout, row * layout.bytes_per_row, data, base).adjust_cell_length(width, base)

    def _row_strip(self, layout: HexLayout, offset: int, data: bytes, base: Style) -> Strip:
        """Build the strip of the row at `offset` holding `data` with the styles cursor > mark > base."""
        text = format_row(layout, offset, data)
        styles = [base] * len(text)
        offset_style = self.get_component_rich_style("hex-view--offset")
        separator_style = self.get_component_rich_style("hex-view--separator")
        for x in range(layout.offset_digits):
            styles[x] = offset_style
        if data:
            styles[layout.char_x - 1] = separator_style
            styles[layout.char_x + len(data)] = separator_style
        cursor = self.cursor
        if offset <= cursor.offset <= offset + len(data) and cursor.offset < offset + layout.bytes_per_row:
            self._style_cursor(layout, styles, cursor, cursor.offset - offset, at_end=cursor.offset >= self._length)
        segments: list[Segment] = []
        start = 0
        for x in range(1, len(text) + 1):
            if x == len(text) or styles[x] != styles[start]:
                segments.append(Segment(text[start:x], styles[start]))
                start = x
        return Strip(segments, len(text))

    def _style_cursor(self, layout: HexLayout, styles: list[Style], cursor: HexCursor, index: int, *, at_end: bool) -> None:
        """Style the cursor cell in the active pane and mark the same byte elsewhere (the cursor is a mark without focus)."""
        mark = self.get_component_rich_style("hex-view--mark")
        active = self.get_component_rich_style("hex-view--cursor") if self.has_focus else mark
        if cursor.pane == "hex":
            styles[layout.digit_x(index, cursor.nibble)] = active
            if not at_end:
                styles[layout.digit_x(index, 1 - cursor.nibble)] = mark
            styles[layout.char_cell_x(index)] = mark
        else:
            styles[layout.char_cell_x(index)] = active
            if not at_end:
                styles[layout.digit_x(index, 0)] = mark
                styles[layout.digit_x(index, 1)] = mark

    # -- events ---------------------------------------------------------------------------------------------------------

    async def on_event(self, event: events.Event) -> None:
        """Tell the scans that the user is interacting (they give way to the UI thread), then handle the event."""
        if isinstance(event, events.Key | events.MouseEvent):
            self._document_provider().foreground.touch()
        await super().on_event(event)

    def on_resize(self) -> None:
        self._sync()

    def on_show(self) -> None:
        self.follow_document()

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()
