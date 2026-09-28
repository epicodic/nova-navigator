"""LocalCopiesDialog — manage local copies of non-local files opened for external editing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Label

from nova_navigator.local_copies import CopyEntry, CopyStatus, LocalCopyManager
from nova_widgets import Button, DataTable

from .dialog import DefaultButton, Dialog
from .message_box import MessageBox


class LocalCopiesDialog(Dialog):
    """Lists every open local copy of a non-local file and lets the user manage it.

    Rows come straight from :attr:`LocalCopyManager.entries`; the table is rebuilt after
    every action and on a 1 s interval so background syncs are reflected without user input.
    """

    DEFAULT_CSS = """
    LocalCopiesDialog {
        #dialog_box {
            width: 90%;
            height: 75%;
        }

        DataTable {
            height: 1fr;
            width: 1fr;
        }

        #local_copies_empty {
            height: 1fr;
            content-align: center middle;
            color: $text-muted;
        }

        #local_copies_actions {
            height: 3;
        }
    }
    """

    _manager: LocalCopyManager
    _reopen: Callable[[CopyEntry], Awaitable[None]]
    _table: DataTable
    _empty_label: Label
    _btn_sync: Button
    _btn_reopen: Button
    _btn_close: Button
    _btn_discard: Button
    _entries: list[CopyEntry]

    def __init__(
        self,
        manager: LocalCopyManager,
        reopen: Callable[[CopyEntry], Awaitable[None]],
    ) -> None:
        super().__init__(title="Local Copies", buttons=[DefaultButton.CLOSE])
        self._manager = manager
        self._reopen = reopen
        self._entries = []

    def compose_content(self) -> ComposeResult:
        self._table = DataTable(id="local_copies_table", cursor_type="row", expand_column=1)
        self._empty_label = Label("No local copies.", id="local_copies_empty")
        self._btn_sync = Button("Sync now", id="sync_now")
        self._btn_reopen = Button("Reopen", id="reopen")
        self._btn_close = Button("Close copy", id="close_copy")
        self._btn_discard = Button("Discard", id="discard", variant="error")
        yield self._table
        yield self._empty_label
        yield Horizontal(self._btn_sync, self._btn_reopen, self._btn_close, self._btn_discard, id="local_copies_actions")

    def on_mount(self) -> None:
        self._table.add_columns("File", "Source", "Status", "Error")
        self._refresh()
        self.set_interval(1.0, self._refresh)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_button_states()

    @on(Button.Pressed, "#sync_now")
    async def _on_sync_now(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        await self._manager.sync_now(entry)
        self._refresh()

    @on(Button.Pressed, "#reopen")
    async def _on_reopen(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        # The external editor launch suspends the TUI, so the dialog must be gone first.
        self.dismiss(None)
        await self._reopen(entry)

    @on(Button.Pressed, "#close_copy")
    async def _on_close_copy(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        await self._manager.close(entry)
        self._refresh()

    @on(Button.Pressed, "#discard")
    @work
    async def _on_discard(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        if entry.has_unsynced_changes or (not entry.copy.read_only and entry.copy.is_modified()):
            confirmed = await MessageBox(
                f"Discard the local copy of {entry.copy.source.name!r}? Unsynced changes will be lost.",
                title="Discard Local Copy",
                buttons=[DefaultButton.OK, DefaultButton.CANCEL],
                variant="warning",
            ).run()
            if confirmed != DefaultButton.OK:
                return
        await self._manager.discard(entry)
        self._refresh()

    def _selected(self) -> CopyEntry | None:
        index = self._table.cursor_row
        if 0 <= index < len(self._entries):
            return self._entries[index]
        return None

    def _refresh(self) -> None:
        """Rebuild the table from the manager's current entries, preserving the cursor row."""
        cursor_row = self._table.cursor_row
        self._entries = list(self._manager.entries)
        self._table.clear(columns=False)
        for entry in self._entries:
            copy = entry.copy
            self._table.add_row(copy.source.name, copy.source.uri, entry.status.value, entry.error or "")
        has_entries = bool(self._entries)
        self._table.display = has_entries
        self._empty_label.display = not has_entries
        if has_entries:
            self._table.move_cursor(row=max(0, min(cursor_row, len(self._entries) - 1)), scroll=False)
        self._update_button_states()

    def _update_button_states(self) -> None:
        entry = self._selected()
        self._btn_sync.disabled = entry is None or entry.status in {CopyStatus.READ_ONLY, CopyStatus.SYNCING}
        self._btn_reopen.disabled = entry is None
        self._btn_close.disabled = entry is None
        self._btn_discard.disabled = entry is None
