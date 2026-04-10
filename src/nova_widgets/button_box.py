from __future__ import annotations

from typing import Any, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button


class ButtonBox(Widget):
    """A grid of buttons with arrow-key navigation.

    Args:
        rows: Either a list of rows (each row is a list of Button widgets),
            or a flat list of Button widgets (treated as a single row).
    """

    DEFAULT_CSS = """
    ButtonBox {
        height: auto;

        Vertical {
            height: auto;
        }

        .button-box-row {
            height: auto;
            align-horizontal: center;
        }

        Button {
            width: auto;
            margin: 0 1;
        }
    }
    """

    BINDINGS: ClassVar = [
        Binding("left", "focus_left", show=False),
        Binding("right", "focus_right", show=False),
        Binding("up", "focus_up", show=False),
        Binding("down", "focus_down", show=False),
    ]

    def __init__(
        self,
        rows: list[list[Button]] | list[Button],
        *,
        id: str | None = None,
        classes: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(id=id, classes=classes, **kwargs)
        if rows and isinstance(rows[0], Button):
            self._rows: list[list[Button]] = [list(cast("list[Button]", rows))]
        else:
            self._rows = [list(row) for row in cast("list[list[Button]]", rows)]

    def compose(self) -> ComposeResult:
        with Vertical():
            for row in self._rows:
                with Horizontal(classes="button-box-row"):
                    yield from row

    def _focused_position(self) -> tuple[int, int] | None:
        """Return (row_idx, col_idx) of the focused button, or None."""
        focused = self.app.focused
        for r, row in enumerate(self._rows):
            for c, btn in enumerate(row):
                if btn is focused:
                    return r, c
        return None

    def action_focus_left(self) -> None:
        pos = self._focused_position()
        if pos is None:
            return
        r, c = pos
        if c > 0:
            self._rows[r][c - 1].focus()

    def action_focus_right(self) -> None:
        pos = self._focused_position()
        if pos is None:
            return
        r, c = pos
        if c < len(self._rows[r]) - 1:
            self._rows[r][c + 1].focus()

    def action_focus_up(self) -> None:
        pos = self._focused_position()
        if pos is None:
            return
        r, c = pos
        if r > 0:
            target_row = self._rows[r - 1]
            target_row[min(c, len(target_row) - 1)].focus()

    def action_focus_down(self) -> None:
        pos = self._focused_position()
        if pos is None:
            return
        r, c = pos
        if r < len(self._rows) - 1:
            target_row = self._rows[r + 1]
            target_row[min(c, len(target_row) - 1)].focus()
