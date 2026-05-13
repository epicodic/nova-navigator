"""ConnectToDialog — pick a saved remote connection."""

from __future__ import annotations

from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import Button, Label, ListItem, ListView

from nova_navigator.config.remotes import RemoteConfig, RemoteConnection
from nova_navigator.icons import ico_

from .dialog import DefaultButton, Dialog


class _RemoteListItem(ListItem):
    """A list item representing a single remote connection."""

    connection: RemoteConnection

    def __init__(self, connection: RemoteConnection) -> None:
        icon = ico_(connection.icon).glyph if connection.icon else ico_("remote").glyph
        super().__init__(Label(f"{icon} {connection.name}"))
        self.connection = connection


class _RemoteListView(ListView):
    """Private list view for ConnectToDialog with double-click tracking."""

    _DOUBLE_CLICK: ClassVar[int] = 2

    class SelectionConfirmed(Message):
        """Emitted when the user confirms a connection (double-click or Enter)."""

        def __init__(self, connection: RemoteConnection) -> None:
            super().__init__()
            self.connection = connection

    def __init__(self, *items: _RemoteListItem, id: str | None = None) -> None:
        super().__init__(*items, id=id)
        self._click_is_double: bool = False
        self._last_event_was_click: bool = False

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        # Single click → highlight only. Double-click or Enter → accept.
        if self._last_event_was_click:
            self._last_event_was_click = False
            if not self._click_is_double:
                return
        item = event.item
        if isinstance(item, _RemoteListItem):
            self.post_message(self.SelectionConfirmed(item.connection))

    def on_click(self, event: events.Click) -> None:
        self._click_is_double = event.chain == self._DOUBLE_CLICK
        self._last_event_was_click = True


class ConnectToDialog(Dialog):
    """Modal dialog for picking a saved remote connection."""

    DEFAULT_CSS = """
    ConnectToDialog {
        #dialog_box { width: 40; height: auto; }
        #remote_list { height: auto; max-height: 20; border: inner $surface; }
    }
    """

    _remotes: RemoteConfig
    selected_connection: RemoteConnection | None

    def __init__(self, remotes: RemoteConfig) -> None:
        super().__init__(title="Connect To", buttons=[DefaultButton.OK, DefaultButton.CANCEL])
        self._remotes = remotes
        self.selected_connection = None

    def compose_content(self) -> ComposeResult:
        connections: list[RemoteConnection] = self._remotes._items
        if not connections:
            yield Label("No remotes configured.")
            return
        yield _RemoteListView(
            *[_RemoteListItem(c) for c in connections],
            id="remote_list",
        )

    def on_mount(self) -> None:
        connections: list[RemoteConnection] = self._remotes._items
        if not connections:
            ok = self.query_one("#OK")
            ok.disabled = True

    def on__remote_list_view_selection_confirmed(self, event: _RemoteListView.SelectionConfirmed) -> None:
        self.selected_connection = event.connection
        self.dismiss("OK")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._button_accept and event.button.id == self._button_accept.id:
            event.stop()
            self.action_accept_dialog()
        else:
            super().on_button_pressed(event)

    def action_accept_dialog(self) -> None:
        if not self._remotes._items:
            return  # OK is disabled; guard against priority Enter binding
        lv = self.query_one("#remote_list", _RemoteListView)
        item = lv.highlighted_child
        if isinstance(item, _RemoteListItem):
            self.selected_connection = item.connection
        self.dismiss("OK")
