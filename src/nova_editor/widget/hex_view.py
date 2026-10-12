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
from textual.binding import Binding, BindingType
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip

from nova_editor.core import Content, SourceChanged
from nova_editor.document import HexEdit, LazyDocument, RowUnavailable
from nova_editor.document._history import HistoryRolledBack
from nova_editor.widget.hex_input import plan_digit
from nova_editor.widget.hex_layout import HexLayout, Pane, choose_layout, format_row, total_rows

_WINDOW_SCREENS_BEFORE = 1
"""Screens of rows the window holds above the first visible row."""
_WINDOW_SCREENS_AFTER = 2
"""Screens of rows the window holds from the first visible row on (the visible screen and the next)."""

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
"""The characters typed as hex digits in the hex pane."""

_FIRST_PRINTABLE = 0x20
"""The first character typed in the character pane (space)."""
_LAST_PRINTABLE = 0x7E
"""The last character typed in the character pane (tilde)."""

_GROUP_BYTES = 8
"""Bytes per hex group; the space between two selected bytes is highlighted only inside a group."""

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

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left", "move('left')", show=False),
        Binding("right", "move('right')", show=False),
        Binding("up", "move('up')", show=False),
        Binding("down", "move('down')", show=False),
        Binding("pageup", "move('page_up')", show=False),
        Binding("pagedown", "move('page_down')", show=False),
        Binding("home", "move('home')", show=False),
        Binding("end", "move('end')", show=False),
        Binding("ctrl+home", "move('start')", show=False),
        Binding("ctrl+end", "move('finish')", show=False),
        Binding("shift+left", "move('left', True)", show=False),
        Binding("shift+right", "move('right', True)", show=False),
        Binding("shift+up", "move('up', True)", show=False),
        Binding("shift+down", "move('down', True)", show=False),
        Binding("shift+pageup", "move('page_up', True)", show=False),
        Binding("shift+pagedown", "move('page_down', True)", show=False),
        Binding("shift+home", "move('home', True)", show=False),
        Binding("shift+end", "move('end', True)", show=False),
        Binding("ctrl+shift+home", "move('start', True)", show=False),
        Binding("ctrl+shift+end", "move('finish', True)", show=False),
        Binding("tab,shift+tab", "switch_pane", show=False),
        Binding("ctrl+a", "select_all", show=False),
        Binding("insert", "toggle_insert", show=False),
        Binding("delete", "delete_forward", show=False),
        Binding("backspace", "delete_backward", show=False),
        Binding("ctrl+z,super+z", "undo", show=False),
        Binding("ctrl+y,super+y", "redo", show=False),
    ]
    """Navigation, Select All, Insert, Delete, Backspace, Undo and Redo; typed digits and characters are handled by `_on_key`.

    The clipboard is not bound here yet."""

    class Moved(Message):
        """The cursor or the selection changed."""

        def __init__(self, cursor: HexCursor, selection: ByteSelection | None) -> None:
            super().__init__()
            self.cursor = cursor
            """The cursor after the change."""
            self.selection = selection
            """The selection after the change."""

    class Edited(Message):
        """The document was changed by an edit made in this view."""

    class ModeChanged(Message):
        """Insert was toggled."""

        def __init__(self, insert: bool) -> None:
            super().__init__()
            self.insert = insert
            """Whether the view is in insert mode now."""

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
        """Insert (True) or overwrite (False) for typing."""
        self.is_read_only: Callable[[], bool] = lambda: False
        """Whether the document is read-only; typing, Delete and Backspace do nothing then. Looked up at every key."""
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
        self._dragging = False
        """Whether a mouse button pressed on the view is held."""
        self._drag_origin: int | None = None
        """The byte pressed at the start of a drag, or `None` for a Shift-press that extends the selection."""
        self._drag_left = False
        """Whether the pointer has left the pressed byte during the drag (a click alone selects nothing)."""
        self._reveal = False
        """Whether `_sync` moved the cursor and a scroll to it is scheduled."""
        self._half: tuple[int, int] | None = None
        """`(offset, revision)` of a byte whose high digit was typed just now and still waits for its low digit, or `None`."""

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

    def set_cursor(self, offset: int, nibble: Literal[0, 1] = 0, *, pane: Pane | None = None, select: bool = False) -> None:
        """Move the cursor without scrolling; post `Moved` when the cursor or the selection changed.

        Args:
            offset: The target byte offset; clamped to `[0, length]`.
            nibble: The digit in the hex pane; forced to 0 in the character pane and at the end of the document.
            pane: The target pane; `None` keeps the present one.
            select: Extend the selection from the present cursor (True) or clear it (False).
        """
        self._sync()
        before = (self.cursor, self.selection)
        target_pane = self.cursor.pane if pane is None else pane
        offset = max(0, min(offset, self._length))
        if offset == self._length or target_pane == "char":
            nibble = 0
        if select:
            if self._anchor is None:
                self._anchor = self.cursor.offset
        else:
            self._anchor = None
        self.cursor = HexCursor(offset, nibble, target_pane)
        self._changed(before)

    def select_all(self) -> None:
        """Select every byte: anchor 0, cursor at the end of the document; reads nothing and does not scroll."""
        self._sync()
        before = (self.cursor, self.selection)
        self._anchor = 0
        self.cursor = HexCursor(self._length, 0, self.cursor.pane)
        self._changed(before)

    def goto(self, offset: int, *, select: bool = False) -> None:
        """Move the cursor to the high digit of `offset` and scroll it into view, centring a far target."""
        self._move(offset, 0, None, select)

    def scroll_to_cursor(self, center: bool = False) -> None:
        """Scroll the cursor row into view: to the top when above, to the bottom when below, or centred."""
        height = self.scrollable_content_region.height
        if height <= 0:
            return
        row = self.cursor.offset // self._layout.bytes_per_row
        top = self.scroll_offset.y
        if center:
            target = max(0, row - height // 2)
        elif row < top:
            target = row
        elif row >= top + height:
            target = row - height + 1
        else:
            return
        if target != top:
            self.scroll_to(y=target, animate=False, immediate=True)

    def action_select_all(self) -> None:
        """Select every byte (Ctrl+A, or the keymap's `select_all`)."""
        self.select_all()

    def action_move(self, move: str, select: bool = False) -> None:
        """Move the cursor by a navigation key (REQ-8); with `select` extend the selection by whole bytes (REQ-14).

        Args:
            move: One of `left`, `right`, `up`, `down`, `page_up`, `page_down`, `home`, `end`, `start`, `finish`.
            select: True for the Shift variant.
        """
        self._sync()
        cursor = self.cursor
        length = self._length
        bytes_per_row = self._layout.bytes_per_row
        row, column = divmod(cursor.offset, bytes_per_row)
        nibble: Literal[0, 1] = 0 if select else cursor.nibble
        if move in ("left", "right"):
            step = -1 if move == "left" else 1
            if cursor.pane == "hex" and not select:
                position = max(0, min(cursor.offset * 2 + cursor.nibble + step, 2 * length))
                self._move(position // 2, 1 if position % 2 else 0, None, select)
            else:
                self._move(cursor.offset + step, 0, None, select)
        elif move in ("up", "down", "page_up", "page_down"):
            height = max(1, self.scrollable_content_region.height)
            distance = height if move.startswith("page") else 1
            target_row = max(0, min(row + (distance if move.endswith("down") else -distance), length // bytes_per_row))
            if target_row == row and self.selection is None:
                return
            if move.startswith("page"):
                self.scroll_to(y=self.scroll_offset.y + target_row - row, animate=False, immediate=True)
            self._move(target_row * bytes_per_row + column, nibble, None, select)
        elif move == "home":
            self._move(row * bytes_per_row, 0, None, select)
        elif move == "end":
            target = min(row * bytes_per_row + bytes_per_row - 1, length)
            last_digit: Literal[0, 1] = 1 if cursor.pane == "hex" and not select and target < length else 0
            self._move(target, last_digit, None, select)
        elif move == "start":
            self._move(0, 0, None, select)
        elif move == "finish":
            self._move(length, 0, None, select)

    def action_switch_pane(self) -> None:
        """Switch between the hex and the character pane at the same offset (Tab, Shift+Tab)."""
        self._sync()
        before = (self.cursor, self.selection)
        pane: Pane = "char" if self.cursor.pane == "hex" else "hex"
        self.cursor = HexCursor(self.cursor.offset, 0, pane)
        self._changed(before)

    def _changed(self, before: tuple[HexCursor, ByteSelection | None]) -> None:
        """Repaint and post `Moved` when the cursor or the selection differ from `before`."""
        after = (self.cursor, self.selection)
        if after != before:
            self._half = None
            self._document_provider().history.checkpoint()
            self.refresh()
            self.post_message(self.Moved(*after))

    def _move(self, offset: int, nibble: Literal[0, 1], pane: Pane | None, select: bool) -> None:
        """Move the cursor and scroll it into view; a target more than one screen outside the view is centred."""
        self.set_cursor(offset, nibble, pane=pane, select=select)
        self._reveal_cursor()

    def _reveal_cursor(self) -> None:
        """Scroll the cursor into view; a cursor more than one screen outside the view is centred."""
        height = self.scrollable_content_region.height
        row = self.cursor.offset // self._layout.bytes_per_row
        top = self.scroll_offset.y
        self.scroll_to_cursor(center=row < top - height or row >= top + 2 * height)

    def follow_document(self) -> None:
        """Pick up a replaced or changed document and repaint."""
        self._sync()
        self.refresh()

    # -- editing --------------------------------------------------------------------------------------------------------

    def action_toggle_insert(self) -> None:
        """Switch between overwrite and insert mode (Insert); a run of typing never spans a mode change."""
        self._document_provider().history.checkpoint()
        self._half = None
        self.insert_mode = not self.insert_mode
        self.post_message(self.ModeChanged(self.insert_mode))

    def action_delete_forward(self) -> None:
        """Delete the selection, or the byte under the cursor (Delete); nothing at the end position."""
        self._delete(backward=False)

    def action_delete_backward(self) -> None:
        """Delete the selection, or the byte before the cursor (Backspace); nothing at offset 0."""
        self._delete(backward=True)

    def _delete(self, *, backward: bool) -> None:
        self._sync()
        if self.is_read_only():
            return
        selection = self.selection
        offset = self.cursor.offset
        if selection is not None:
            start, end = selection.start, selection.end
        elif backward:
            if offset == 0:
                return
            start, end = offset - 1, offset
        else:
            if offset >= self._length:
                return
            start, end = offset, offset + 1
        self._edit(start, end, b"", kind="delete", cursor=(start, 0))

    async def _on_key(self, event: events.Key) -> None:
        """Type a hex digit (hex pane) or a printable ASCII character (character pane) at the cursor.

        Keys with a binding, and keys without a character, are not typing. In the character pane any other character
        (non-ASCII, Enter, an unbound control character) changes nothing and rings the bell; in the hex pane it is ignored.
        """
        character = event.character
        if character is None or self.is_read_only():
            return
        if self.cursor.pane == "hex":
            if len(character) == 1 and character in _HEX_DIGITS:
                event.stop()
                event.prevent_default()
                self._type_digit(int(character, 16))
            return
        if event.is_printable and len(character) == 1 and _FIRST_PRINTABLE <= ord(character) <= _LAST_PRINTABLE:
            event.stop()
            event.prevent_default()
            self._type_character(ord(character))
        elif event.key != "escape" and event.key not in self.app.active_bindings:
            event.stop()
            event.prevent_default()
            self.app.bell()

    def _typing_position(self) -> int:
        """The offset typing starts at: the start of the selection, which typing collapses (I6), else the cursor."""
        selection = self.selection
        return self.cursor.offset if selection is None else selection.start

    def _type_digit(self, digit: int) -> None:
        self._sync()
        offset, nibble = self._typing_position(), self.cursor.nibble if self.selection is None else 0
        current: int | None = None
        if offset < self._length:
            data = self._row_bytes(offset // self._layout.bytes_per_row)
            if data is None:
                return
            current = data[offset % self._layout.bytes_per_row]
        document = self._document_provider()
        half = self._half == (offset, document.revision)
        plan = plan_digit(digit, offset, nibble, self._length, self.insert_mode, current, half=half)
        cursor = (plan.cursor_offset, 1 if plan.cursor_nibble else 0)
        waits_for_low_digit = nibble == 0 and (self.insert_mode or offset >= self._length)
        self._edit(plan.start, plan.end, plan.data, kind="type", completes=plan.completes, cursor=cursor, half=waits_for_low_digit)

    def _type_character(self, value: int) -> None:
        self._sync()
        offset = self._typing_position()
        end = offset + 1 if offset < self._length and not self.insert_mode else offset
        self._edit(offset, end, bytes((value,)), kind="type", cursor=(offset + 1, 0))

    def _edit(
        self,
        start: int,
        end: int,
        data: bytes | Content,
        *,
        kind: Literal["type", "delete", "paste"],
        cursor: tuple[int, Literal[0, 1]],
        completes: bool = False,
        half: bool = False,
    ) -> bool:
        """Replace the bytes `[start, end)` by `data`, record the history entry and place the cursor.

        Args:
            start: First byte replaced.
            end: End of the replaced range (exclusive).
            data: The new bytes, or content that is already stored.
            kind: What produced the edit (the history groups by it).
            cursor: Where the cursor goes: the byte offset and the digit.
            completes: Whether this is the low digit of a byte whose high digit was typed just before.
            half: Whether the edit leaves a byte that waits for its low digit.

        Returns:
            True when the document was changed; False when the view is read-only, the edit was refused or nothing happened.
        """
        self._sync()
        if self.is_read_only():
            return False
        document = self._document_provider()
        try:
            content = data if isinstance(data, Content) else document.content_of_bytes(data)
            result = document.splice_bytes(start, end, content)
        except SourceChanged as error:
            if not self._reported:
                self._reported = True
                if self.on_source_changed is not None:
                    self.on_source_changed(error)
            return False
        except (RowUnavailable, ValueError) as error:
            self._refuse(str(error))
            return False
        if result.start_byte is None or result.removed is None or result.inserted is None:
            raise RuntimeError("splice_bytes returned no byte facts")
        document.history.record(HexEdit(result.start_byte, result.removed, result.inserted, kind=kind, completes=completes))
        self._adopt_own_change()
        offset, nibble = cursor
        offset = max(0, min(offset, self._length))
        if offset == self._length or self.cursor.pane == "char":
            nibble = 0
        self._anchor = None
        self.cursor = HexCursor(offset, nibble, self.cursor.pane)
        self._half = (offset, document.revision) if half else None
        self.scroll_to_cursor()
        self.refresh()
        self.post_message(self.Edited())
        return True

    def action_undo(self) -> None:
        """Undo the most recent batch of edits, also those made in the text view (Ctrl+Z); the cursor goes to the place of the edit."""
        self._move_history("undo")

    def action_redo(self) -> None:
        """Redo the most recently undone batch of edits (Ctrl+Y); the cursor goes behind the redone bytes."""
        self._move_history("redo")

    def _move_history(self, direction: Literal["undo", "redo"]) -> None:
        """Undo or redo one batch through the history of the document, then bring the view in step.

        A refused step (the edit lock) rings the bell and warns; the history is then unchanged. A step that could not be rolled back
        puts the document in the failed state, like a changed file.
        """
        self._sync()
        document = self._document_provider()
        history = document.history
        try:
            step = history.undo(document) if direction == "undo" else history.redo(document)
        except RowUnavailable as error:
            self._refuse(str(error))
            return
        except HistoryRolledBack as error:
            reason = f"an undo or redo could not be rolled back: {error}"
            if not self._reported:
                self._reported = True
                if self.on_source_changed is not None:
                    self.on_source_changed(SourceChanged(reason))
            self._refuse(str(error))
            return
        if step is None:
            return
        self._adopt_own_change()
        self._anchor = None
        self._half = None
        self.cursor = HexCursor(max(0, min(step.byte, self._length)), 0, self.cursor.pane)
        self._reveal_cursor()
        self.refresh()
        self.post_message(self.Moved(self.cursor, None))
        self.post_message(self.Edited())

    def _adopt_own_change(self) -> None:
        """Take over the revision and length of an edit made here; the view keeps the byte offset at the top."""
        document = self._document_provider()
        self._revision = document.revision
        self._length = document.length
        self._drop_window()
        self._relayout(self.scrollable_content_region.width)

    def _refuse(self, reason: str) -> None:
        """Ring the bell and warn that an edit was refused (the edit lock, a closed document)."""
        self.app.bell()
        self.notify(f"Edit refused: {reason}.", severity="warning")

    # -- synchronisation ------------------------------------------------------------------------------------------------

    def _sync(self) -> None:
        """Compare the document identity, its revision and the width with what was last drawn, and adapt."""
        document = self._document_provider()
        width = self.scrollable_content_region.width
        if document is not self._document:
            self._document = document
            self._reported = False
            self._anchor = None
            self._half = None
            self._revision = document.revision
            self._length = document.length
            self._drop_window()
            before = self.cursor
            self.cursor = HexCursor(min(self.cursor.offset, self._length), 0, self.cursor.pane)
            self._relayout(width)
            if self.cursor != before:
                self._schedule_reveal()
        elif document.revision != self._revision:
            self._revision = document.revision
            self._length = document.length
            self._drop_window()
            before = self.cursor
            self._clamp_cursor()
            self._relayout(width)
            if self.cursor != before:
                self._schedule_reveal()
        elif width != self._width:
            self._relayout(width)

    def _schedule_reveal(self) -> None:
        """Scroll to the cursor after the next refresh (once), because `_sync` moved it and may run while the view renders."""
        if not self._reveal:
            self._reveal = True
            self.call_after_refresh(self._do_reveal)

    def _do_reveal(self) -> None:
        self._reveal = False
        self._reveal_cursor()

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
        """Build the strip of the row at `offset` holding `data` with the styles cursor > mark > selection > base."""
        text = format_row(layout, offset, data)
        styles = [base] * len(text)
        offset_style = self.get_component_rich_style("hex-view--offset")
        separator_style = self.get_component_rich_style("hex-view--separator")
        for x in range(layout.offset_digits):
            styles[x] = offset_style
        if data:
            styles[layout.char_x - 1] = separator_style
            styles[layout.char_x + len(data)] = separator_style
        selection = self.selection
        if selection is not None and selection.start < offset + len(data) and offset < selection.end:
            self._style_selection(layout, styles, max(selection.start, offset) - offset, min(selection.end, offset + len(data)) - offset)
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

    def _style_selection(self, layout: HexLayout, styles: list[Style], first: int, end: int) -> None:
        """Style the selected bytes `[first, end)` of the row in both panes, and the space between two of a group."""
        selected = self.get_component_rich_style("hex-view--selection")
        for index in range(first, end):
            high = layout.digit_x(index, 0)
            styles[high] = styles[high + 1] = selected
            if index + 1 < end and (index + 1) % _GROUP_BYTES:
                styles[high + 2] = selected
            styles[layout.char_cell_x(index)] = selected

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

    def _hit(self, x: int, y: int) -> tuple[Pane, int, Literal[0, 1]] | None:
        """Map a cell of the view to `(pane, offset, nibble)`; `None` for the offset column, the gaps and the `|` marks.

        A row above or below the view is the row just outside it (a drag moves one row per event); a position right of
        the last byte, or below the last row, is the end-of-document position.
        """
        layout = self._layout
        height = self.scrollable_content_region.height
        top = self.scroll_offset.y
        row = top - 1 if y < 0 else top + height if y >= height else top + y
        last_row = self._length // layout.bytes_per_row
        located = layout.locate(x)
        if located is None:
            if row <= last_row:
                return None
            located = (self.cursor.pane, 0, 0)
        pane, index, nibble = located
        if row > last_row:
            return pane, self._length, 0
        offset = max(0, row) * layout.bytes_per_row + index
        if offset >= self._length:
            return pane, self._length, 0
        return pane, offset, 1 if nibble else 0

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        """Place the cursor (Shift: extend the selection from it) and start a drag."""
        self._sync()
        hit = self._hit(event.x, event.y)
        if hit is None:
            return
        pane, offset, nibble = hit
        if event.shift:
            self._move(offset, 0, pane, True)
            self._drag_origin = None
        else:
            self._move(offset, nibble, pane, False)
            self._drag_origin = offset
        self._dragging = True
        self._drag_left = False
        self.capture_mouse()

    async def _on_mouse_move(self, event: events.MouseMove) -> None:
        """Extend the selection to the byte under the pointer while dragging (inclusive of both ends)."""
        if not self._dragging:
            return
        hit = self._hit(event.x, event.y)
        if hit is None:
            return
        _, offset, _ = hit
        pane = self.cursor.pane  # a drag stays in the pane it started in
        origin = self._drag_origin
        if origin is None:  # Shift-press: the anchor stays, the cursor follows the pointer
            self._move(offset, 0, pane, True)
            return
        if offset == origin and not self._drag_left:
            return
        self._drag_left = True
        before = (self.cursor, self.selection)
        if offset >= origin:
            self._anchor = origin
            target = min(offset + 1, self._length)
        else:
            self._anchor = min(origin + 1, self._length)
            target = offset
        self.cursor = HexCursor(target, 0, pane)
        self._changed(before)
        self.scroll_to_cursor()

    async def _on_mouse_up(self, event: events.MouseUp) -> None:
        """End a drag."""
        self._end_drag()

    def _on_hide(self, event: events.Hide) -> None:
        """End a drag when the view is hidden."""
        self._end_drag()

    def _end_drag(self) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()

    def on_resize(self) -> None:
        self._sync()

    def on_show(self) -> None:
        self.follow_document()

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()
