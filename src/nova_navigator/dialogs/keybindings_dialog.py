"""KeybindingsDialog — modal dialog for viewing and editing key bindings."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.widgets import DataTable, Label

from nova_navigator.keymap.config import KeybindingsConfig
from nova_widgets.menu._action import Action

from .dialog import DefaultButton, Dialog


class KeybindingsDialog(Dialog):
    """Modal dialog that shows all configured key bindings and allows editing.

    Displays a table of actions with their current key binding and description.
    Users can select a row to see the action description below the table.
    """

    DEFAULT_CSS = """
    KeybindingsDialog {
        #dialog_box {
            width: 80%;
            height: 70%;
        }

        DataTable {
            height: 1fr;
        }

        #description_label {
            height: 2;
            margin: 0 0 1 0;
            color: $text-muted;
        }
    }
    """

    _actions: list[Action]
    _config: KeybindingsConfig
    _table: DataTable[Any]
    _description_label: Label

    def __init__(
        self,
        actions: list[Action],
        config: KeybindingsConfig,
    ) -> None:
        super().__init__(
            title="Key Bindings",
            buttons=[DefaultButton.OK, DefaultButton.CANCEL],
        )
        self._actions = list(actions)
        self._config = config

    def compose_content(self) -> ComposeResult:
        self._table = DataTable(id="bindings_table")
        self._description_label = Label("", id="description_label")
        yield self._table
        yield self._description_label

    def on_mount(self) -> None:
        self._table.add_columns("Action", "Key Binding", "Contexts")
        bindings = self._config.resolve(self._actions)
        for action in self._actions:
            key = bindings.get(action.name or "", "") if action.name else ""
            contexts = ", ".join(action.contexts) if action.contexts else ""
            self._table.add_row(action.text, key or "(none)", contexts)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._actions):
            action = self._actions[idx]
            self._description_label.update(action.description or "")
