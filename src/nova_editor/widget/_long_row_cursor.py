"""Widget-side bookkeeping of the cursor on one long row (ACT3 design 7): the cursor machine and the provisional window layout.

The class holds no Textual objects. While the cursor is PROVISIONAL (no-wrap only) its column is only an estimate, so the row is drawn
from a byte anchor: the character at byte `left_byte` sits at cell 0 of the text area and the cursor sits `cursor_cells` cells to its
right, with tab stops counted from that character. When the scan reaches the cursor, `exact_left_x` gives the exact display column of
that same character, so scrolling to it leaves the visible text where it was (design 7.3). The tab phase of a provisional window is
an assumption (phase 0 at the left edge); the resolved window can differ in tab width when the true phase is not 0.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from nova_editor.core.text_width import advance_disp, utf8_len
from nova_editor.document._cursor_anchor import CursorMachine, CursorState
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.document._long_row_anchor import LongRowAnchorIndex
from nova_editor.widget._lazy_window import WindowText

_MAX_SPAN_FACTOR = 16
"""A layout is kept only while the cursor lies within this many bytes per visible cell of the left edge."""

_PROGRESS_LIMIT = 0.999999
"""Progress of a pending jump stays below 1: 1 means resolved."""


@dataclass(frozen=True)
class ProvisionalLayout:
    """Where a provisional cursor row is drawn."""

    row: int
    left_byte: int
    """Byte (relative to the row start) of the character at cell 0 of the text area."""
    left_x: int
    """The `scroll_x` that goes with the layout; a different `scroll_x` shifts the window by that many characters."""
    cursor_cells: int
    """Cells from the left edge to the cursor."""


def _prefix_to_byte(text: str, nbytes: int) -> str:
    """Return the leading characters of `text` that start before byte `nbytes`."""
    total = 0
    for position, char in enumerate(text):
        if total >= nbytes:
            return text[:position]
        total += utf8_len(char)
    return text


class LongRowCursor:
    """The cursor machine of the current long row and its provisional layout (created on entering the row, dropped on leaving it)."""

    def __init__(self, lazy: LazyDocument) -> None:
        """Create the holder for `lazy`; no machine exists until the cursor is on a long row."""
        self._lazy = lazy
        self.machine: CursorMachine | None = None
        self.index: LongRowAnchorIndex | None = None
        self.row = -1
        self.layout: ProvisionalLayout | None = None
        self.pending_target: int | None = None
        """Byte (relative to the row start) a pending jump waits for; its progress is `frontier / target`."""
        self.pending_select = False
        """Whether the deferred operation extends the selection."""

    def drop(self) -> None:
        """Forget the machine (the cursor left the row)."""
        self.machine = None
        self.index = None
        self.row = -1
        self.layout = None
        self.pending_target = None

    def track(self, row: int, column: int, *, wrap: bool) -> CursorMachine | None:
        """Return the machine of the cursor location, creating it when the cursor entered a long row; `None` on other rows.

        The machine is kept while the location still carries its column; any other location (a move that did not go through the
        machine, a click) starts a new machine at the byte of that location.
        """
        if not self._lazy.is_long(row):
            self.drop()
            return None
        machine = self.machine
        if machine is not None and self.row == row and machine.anchor.column == column:
            return machine
        index = self._lazy.anchor_index(row)
        relative = index.index.try_char_to_byte(column)
        if relative is None:
            relative = min(index.frontier_byte(), index.row_end_rel)
        self.machine = CursorMachine(row, index, relative, wrap=wrap)
        self.index = index
        self.row = row
        self.layout = None
        self.pending_target = None
        return self.machine

    def provisional_layout(self, row: int) -> ProvisionalLayout | None:
        """Return the layout while `row` is the provisional cursor row, else `None`."""
        machine = self.machine
        layout = self.layout
        if machine is None or layout is None or layout.row != row or machine.state is CursorState.RESOLVED:
            return None
        return layout

    def progress(self) -> float:
        """Return the fraction of the pending target that the scan has covered, in [0, 1)."""
        index = self.index
        target = self.pending_target
        if index is None or target is None:
            return 0.0
        if target <= 0:
            return 0.0
        return min(max(index.frontier_byte() / target, 0.0), _PROGRESS_LIMIT)

    def _text_between(self, left: int, cursor: int) -> str:
        """Return the characters between two byte offsets (`left` on a character boundary)."""
        index = self.index
        if index is None or cursor <= left:
            return ""
        text, _skipped = index.index.decode_from_byte(left, cursor - left)
        return _prefix_to_byte(text, cursor - left)

    def _cells_between(self, left: int, cursor: int, tab_width: int, visible: int) -> int | None:
        """Return the cells from `left` to `cursor`, or `None` when the span is too long to be a window."""
        if cursor - left > _MAX_SPAN_FACTOR * (visible + 1):
            return None
        return advance_disp(self._text_between(left, cursor), 0, tab_width)

    def place(self, cursor_rel: int, cx_est: int, visible: int, tab_width: int, scroll_x: int) -> ProvisionalLayout | None:
        """Choose the layout for the cursor at `cursor_rel`: keep the old one while the cursor fits, else put the cursor at an edge.

        Args:
            cursor_rel: Cursor byte relative to the row start.
            cx_est: Estimated virtual x of the cursor (gives `left_x`).
            visible: Visible text width in cells.
            tab_width: Tab stop width.
            scroll_x: Current horizontal scroll; a layout is only kept while it still matches it.

        Returns:
            The new layout (also stored), or `None` when the row has no anchor index.
        """
        index = self.index
        if index is None:
            return None
        visible = max(1, visible)
        previous = self.layout
        if previous is not None and previous.row == self.row and previous.left_byte <= cursor_rel and scroll_x == previous.left_x:
            cells = self._cells_between(previous.left_byte, cursor_rel, tab_width, visible)
            if cells is not None and cells <= visible - 1:
                self.layout = replace(previous, cursor_cells=cells)
                return self.layout
        moved_left = previous is not None and previous.row == self.row and cursor_rel < previous.left_byte
        target = min(visible - 1, visible // 4) if moved_left else visible - 1
        left = index.step(cursor_rel, -target)
        text = self._text_between(left, cursor_rel)
        drop = 0
        while drop < len(text) and advance_disp(text[drop:], 0, tab_width) > target:
            drop += 1
        left += utf8_len(text[:drop])
        cells = advance_disp(text[drop:], 0, tab_width)
        self.layout = ProvisionalLayout(self.row, left, max(0, cx_est - cells), cells)
        return self.layout

    def adopt_scroll(self, scroll_x: int) -> None:
        """Record the `scroll_x` the widget actually reached (Textual clamps it) as the layout's `left_x`."""
        if self.layout is not None:
            self.layout = replace(self.layout, left_x=scroll_x)

    def window(self, row: int, scroll_x: int, visible: int, cursor_column: int) -> WindowText | None:
        """Return the text to draw for a provisional cursor row, or `None` when the row is drawn the normal way.

        The text starts at the character at cell 0 (moved by `scroll_x - left_x` characters when the user scrolled) and its
        `start_column` is chosen so that `cursor_column - start_column` is the index of the cursor character inside the text.
        """
        layout = self.provisional_layout(row)
        machine = self.machine
        index = self.index
        if layout is None or machine is None or index is None:
            return None
        shift = scroll_x - layout.left_x
        start = index.step(layout.left_byte, shift) if shift else layout.left_byte
        count = visible + 4
        text, skipped = index.index.decode_from_byte(start, count)
        start += skipped
        at_end = start + utf8_len(text) >= index.row_end_rel
        cursor_rel = machine.anchor.byte_rel
        if cursor_rel < start:
            cursor_index = -1
        else:
            cursor_index = len(_prefix_to_byte(text, cursor_rel - start))
        return WindowText(text, cursor_column - cursor_index, scroll_x, 0, at_row_end=at_end, truncated=False, missing=False)

    def exact_left_x(self, row: int, tab_width: int) -> int | None:
        """Return the exact display column of the layout's left character (also after the cursor resolved), or `None` when unknown."""
        layout = self.layout
        index = self.index
        if layout is None or layout.row != row or index is None:
            return None
        column = index.exact_column(layout.left_byte)
        if column is None:
            return None
        return self._lazy.display_column(row, column, tab_width)
