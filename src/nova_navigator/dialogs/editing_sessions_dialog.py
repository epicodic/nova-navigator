"""Dialog for recoverable external-editing sessions."""

from __future__ import annotations

from collections.abc import Callable

from textual import on
from textual.app import ComposeResult
from textual.widgets import Button, Label

from nova_navigator.editing.model import EditingSession, SessionState
from nova_widgets import DataTable

from .dialog import DefaultButton, Dialog


class EditingSessionsDialog(Dialog):
    """Show active local mirrors and provide safe session actions."""

    DEFAULT_CSS = """
    EditingSessionsDialog {
        #dialog_box { width: 90%; height: 75%; }
        DataTable { height: 1fr; }
        #editing_actions { height: 3; }
    }
    """

    def __init__(
        self,
        sessions: list[EditingSession],
        finish: Callable[[str, bool], EditingSession],
        discard: Callable[[str], None],
    ) -> None:
        super().__init__(title="Editing Sessions", buttons=[DefaultButton.CLOSE])
        self._sessions = sessions
        self._finish = finish
        self._discard = discard
        self._table: DataTable
        self._status: Label

    def compose_content(self) -> ComposeResult:
        self._table = DataTable(id="editing_sessions", cursor_type="row", expand_column=0)
        self._status = Label("Select a session, then finish or discard it.", id="editing_status")
        yield self._table
        yield Label("Finish writes a changed editable mirror back to its source.", id="editing_actions")
        yield self._status
        yield Button("Finish editing", id="finish")
        yield Button("Overwrite source", id="overwrite")
        yield Button("Discard", id="discard", variant="error")

    def on_mount(self) -> None:
        self._table.add_columns("Source", "State", "Mode", "Error")
        self._refresh()

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        if event.button.id == "discard":
            session = self._selected()
            if session is not None:
                self._discard(session.session_id)
                self._sessions.remove(session)
                self._refresh()
            return
        if event.button.id in {"finish", "overwrite"}:
            session = self._selected()
            if session is not None:
                updated = self._finish(session.session_id, event.button.id == "overwrite")
                if updated.state is SessionState.COMPLETED:
                    self._sessions.remove(session)
                self._refresh()

    def _selected(self) -> EditingSession | None:
        index = self._table.cursor_row
        return self._sessions[index] if 0 <= index < len(self._sessions) else None

    def _refresh(self) -> None:
        self._table.clear(columns=False)
        for session in self._sessions:
            mode = "read-only" if session.read_only else "editable"
            self._table.add_row(session.source_uri, session.state.value, mode, session.error or "")
        self._status.update(f"{len(self._sessions)} active editing session(s).")
