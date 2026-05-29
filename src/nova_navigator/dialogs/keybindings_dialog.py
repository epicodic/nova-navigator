"""KeybindingsDialog and KeyCaptureDialog — key binding editor dialogs."""

from __future__ import annotations

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.coordinate import Coordinate
from textual.widgets import Label

from nova_navigator.config import conf_
from nova_navigator.keymap.config import KeybindingsConfig
from nova_widgets import DataTable
from nova_widgets.action import Action
from nova_widgets.keymap.key_sequence import KeyChord, KeyFormatStyle, KeySequence

from .dialog import DefaultButton, Dialog

_HINT_NORMAL = "Assign key: double-click / space | Clear binding: delete"
_KEY_CHORD_BADGE_STYLE = "bold white on grey30"


def _get_key_display_style() -> KeyFormatStyle:
    try:
        return conf_.settings.general.key_display_style
    except AttributeError:
        return KeyFormatStyle.CLASSIC


def _format_key_sequence_badges(sequence: KeySequence | None, style: KeyFormatStyle) -> Text | str:
    if sequence is None:
        return "(none)"
    text = Text()
    for index, chord in enumerate(sequence.chords):
        if index > 0:
            text.append(" ")
        text.append(f" {chord.format(style)} ", style=_KEY_CHORD_BADGE_STYLE)
    return text


class KeyCaptureDialog(Dialog):
    """Small modal that captures a key sequence for a single action."""

    DEFAULT_CSS = """
    KeyCaptureDialog {
        #dialog_box { width: 56; height: auto; }
        #action_label { margin: 0 0 1 0; color: $text-muted; }
        #sequence_display { height: 1; margin: 0 0 1 0; color: $primary; text-style: bold; }
        #capture_hint { height: 1; margin: 0 0 1 0; color: $text-muted; }
    }
    """

    _action: Action
    _chords: list[KeyChord]
    _sequence_display: Label

    def __init__(self, action: Action) -> None:
        super().__init__(title="Assign Key Binding", buttons=[DefaultButton.OK, DefaultButton.CANCEL])
        self._action = action
        self._chords = []

    def compose_content(self) -> ComposeResult:
        self._sequence_display = Label("(none)", id="sequence_display")
        yield Label(f"Action:  {self._action.text}", id="action_label")
        yield self._sequence_display
        yield Label("Enter: confirm  Backspace: undo last chord  Escape: cancel", id="capture_hint")

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            self.action_accept_dialog()
        elif event.key == "backspace":
            if self._chords:
                self._chords.pop()
                self._refresh_display()
        elif event.key != "escape":
            self._chords.append(KeyChord.parse(event.key))
            self._refresh_display()
        event.prevent_default()
        event.stop()

    def _refresh_display(self) -> None:
        if self._chords:
            style = _get_key_display_style()
            sequence = KeySequence(tuple(self._chords))
            self._sequence_display.update(_format_key_sequence_badges(sequence, style))
        else:
            self._sequence_display.update("(none)")

    @property
    def value(self) -> KeySequence | None:
        """Return the captured KeySequence, or None if no keys were pressed."""
        return KeySequence(tuple(self._chords)) if self._chords else None


class KeybindingsDialog(Dialog):
    """Modal dialog that shows all configured key bindings and allows editing.

    Displays a table of actions with their current key binding and description.
    Space or double-click on a row opens the key-capture dialog to assign a
    new binding. Delete clears the binding for the selected row.
    """

    DEFAULT_CSS = """
    KeybindingsDialog {
        #dialog_box {
            width: 80%;
            height: 70%;
        }

        DataTable {
            height: 1fr;
            width: 1fr;
        }

        #description_label {
            height: 1;
            margin: 0 0 0 0;
            color: $text-muted;
        }

        #status_label {
            height: 1;
            margin: 0 0 1 0;
            color: $text-muted;
        }
    }
    """

    _actions: list[Action]
    _config: KeybindingsConfig
    _table: DataTable
    _description_label: Label
    _key_map: dict[str, KeySequence]
    _deleted_names: set[str]
    _hovered_row: int | None

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
        self._key_map = {}
        self._deleted_names = set()
        self._hovered_row = None

    def compose_content(self) -> ComposeResult:
        self._table = DataTable(id="bindings_table", cursor_type="row", expand_column=0)
        self._description_label = Label("", id="description_label")
        yield self._table
        yield self._description_label
        yield Label(_HINT_NORMAL, id="status_label")

    def on_mount(self) -> None:
        self._table.add_columns("Action", "Key Binding")
        self._key_map = self._config.resolve(self._actions)
        style = _get_key_display_style()
        for action in self._actions:
            key = self._key_map.get(action.id or "") if action.id else None
            self._table.add_row(action.text, _format_key_sequence_badges(key, style))
        if self._actions:
            self._description_label.update(self._actions[0].description or "")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._hovered_row is None:
            idx = event.cursor_row
            if 0 <= idx < len(self._actions):
                self._description_label.update(self._actions[idx].description or "")

    @on(DataTable.RowHovered)
    def _on_row_hovered(self, event: DataTable.RowHovered) -> None:
        if event.row is not None:
            self._hovered_row = event.row
            if 0 <= event.row < len(self._actions):
                self._description_label.update(self._actions[event.row].description or "")
        else:
            self._hovered_row = None
            idx = self._table.cursor_row
            if 0 <= idx < len(self._actions):
                self._description_label.update(self._actions[idx].description or "")

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "space" and isinstance(self.focused, DataTable):
            await self._open_key_capture()
            event.prevent_default()
            event.stop()
        elif event.key in ("backspace", "delete") and isinstance(self.focused, DataTable):
            idx = self._table.cursor_row
            if 0 <= idx < len(self._actions):
                action = self._actions[idx]
                if action.id:
                    self._key_map.pop(action.id, None)
                    self._deleted_names.add(action.id)
                    self._table.update_cell_at(Coordinate(idx, 1), "(none)")
            event.prevent_default()
            event.stop()

    @on(DataTable.RowDoubleClicked)
    async def _on_row_double_clicked(self) -> None:
        await self._open_key_capture()

    async def _open_key_capture(self) -> None:
        idx = self._table.cursor_row
        if not (0 <= idx < len(self._actions)):
            return
        action = self._actions[idx]
        if not action.id:
            return
        dialog = KeyCaptureDialog(action)
        response = await dialog.run()
        if response == DefaultButton.OK and dialog.value is not None:
            self._key_map[action.id] = dialog.value
            self._deleted_names.discard(action.id)
            style = _get_key_display_style()
            self._table.update_cell_at(Coordinate(idx, 1), _format_key_sequence_badges(dialog.value, style))

    def action_accept_dialog(self) -> None:
        to_save: dict[str, KeySequence | None] = {**self._key_map}
        for name in self._deleted_names:
            to_save[name] = None
        self._config.save(to_save)
        super().action_accept_dialog()
