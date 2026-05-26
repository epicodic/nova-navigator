"""DataTable — Textual DataTable extended with double-click and column auto-expansion."""

from __future__ import annotations

from typing import Any

from textual import events
from textual.message import Message
from textual.widgets import DataTable as _TextualDataTable

_DOUBLE_CLICK_CHAIN = 2


class DataTable(_TextualDataTable[Any]):
    """Textual DataTable with double-click and hover support.

    If `expand_column` is set to a column index, that column auto-expands to
    fill the available widget width whenever the widget is resized.
    """

    class RowDoubleClicked(Message):
        """Emitted when the user double-clicks on a data row."""

    class RowHovered(Message):
        """Emitted when the mouse moves over a data row or leaves the table.

        Attributes:
            row: The 0-based row index under the cursor, or None when the mouse
                has left the table or is not over a data row.
        """

        def __init__(self, row: int | None) -> None:
            super().__init__()
            self.row = row

    _expand_column: int | None
    _hovered_row: int | None

    def __init__(self, *args: Any, expand_column: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._expand_column = expand_column
        self._hovered_row = None

    async def _on_click(self, event: events.Click) -> None:
        await super()._on_click(event)
        meta = event.style.meta
        if event.chain >= _DOUBLE_CLICK_CHAIN and meta.get("row", -1) >= 0:
            self.post_message(self.RowDoubleClicked())

    def on_resize(self) -> None:
        """Adjust the expand column width to fill available space."""
        self._adjust_expand_column()

    def _on_mouse_move(self, event: events.MouseMove) -> None:
        row = event.style.meta.get("row", -1)
        new_hovered = row if row >= 0 else None
        if new_hovered != self._hovered_row:
            self._hovered_row = new_hovered
            self.post_message(self.RowHovered(new_hovered))

    def on_leave(self, event: events.Leave) -> None:
        if self._hovered_row is not None:
            self._hovered_row = None
            self.post_message(self.RowHovered(None))

    def _adjust_expand_column(self) -> None:
        if self._expand_column is None:
            return
        columns = self.ordered_columns
        if not columns or self._expand_column >= len(columns):
            return
        expand_col = columns[self._expand_column]
        other_width = sum(col.get_render_width(self) for i, col in enumerate(columns) if i != self._expand_column)
        available = self.size.width - other_width
        expand_col.width = max(1, available - 2 * self.cell_padding)
        expand_col.auto_width = False
        self.refresh()
