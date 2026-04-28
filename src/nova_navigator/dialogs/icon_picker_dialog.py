from typing import Any

from textual import events, widgets
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.message import Message

from ..decision import Decision
from ..icons import ICONS
from .dialog import Dialog


class _IconCell(widgets.Static, can_focus=False):
    """A single selectable icon cell in the icon picker grid."""

    class Selected(Message, namespace="icon_cell"):
        def __init__(self, icon_name: str) -> None:
            super().__init__()
            self.icon_name = icon_name

    class Hovered(Message, namespace="icon_cell"):
        def __init__(self, icon_name: str) -> None:
            super().__init__()
            self.icon_name = icon_name

    def __init__(self, icon_name: str, glyph: str) -> None:
        super().__init__(glyph, classes="icon-cell")
        self._icon_name = icon_name

    def on_click(self) -> None:
        self.post_message(self.Selected(self._icon_name))

    def on_enter(self, event: events.Enter) -> None:
        if event.node is self:
            self.post_message(self.Hovered(self._icon_name))


class IconPickerDialog(Dialog):
    """Modal dialog for selecting an icon from the ICONS iconset.

    Returns the selected icon name (str) on OK, or dismisses with the
    button id string (e.g. ``"CANCEL"``) if the user cancels.
    """

    DEFAULT_CSS = (
        Dialog.DEFAULT_CSS
        + """
    IconPickerDialog {
        #dialog_box {
            width: 70%;
        }

        #icon_grid {
            layout: grid;
            grid-size: 10;
            height: auto;
            max-height: 20;
            overflow-y: auto;
        }

        .icon-cell {
            width: 4;
            height: 2;
            content-align: center middle;
        }

        .icon-cell:hover {
            background: $accent 30%;
        }

        .icon-cell.-selected {
            background: $primary;
        }

        #icon_status {
            height: 1;
            content-align: center middle;
            color: $text-muted;
        }
    }
    """
    )

    _selected_icon: str | None

    def __init__(
        self,
        title: str = "Select Icon",
        initial_icon: str | None = None,
        id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(title, id=id, buttons=[Decision.OK, Decision.CANCEL], **kwargs)
        self._selected_icon = initial_icon

    def compose_content(self) -> ComposeResult:
        cells = [_IconCell(name, str(ICONS.get_icon(name))) for name, _ in ICONS]
        yield ScrollableContainer(*cells, id="icon_grid")
        yield widgets.Label("", id="icon_status")

    def on_mount(self) -> None:
        if self._selected_icon is not None:
            self._apply_selection(self._selected_icon)

    def on_icon_cell_selected(self, event: _IconCell.Selected) -> None:
        self._selected_icon = event.icon_name
        self._apply_selection(event.icon_name)

    def on_icon_cell_hovered(self, event: _IconCell.Hovered) -> None:
        self.query_one("#icon_status", widgets.Label).update(event.icon_name)

    def _apply_selection(self, icon_name: str) -> None:
        for cell in self.query(_IconCell):
            cell.set_class(cell._icon_name == icon_name, "-selected")

    def action_accept_dialog(self) -> None:
        if self._selected_icon is not None:
            self.dismiss(self._selected_icon)
