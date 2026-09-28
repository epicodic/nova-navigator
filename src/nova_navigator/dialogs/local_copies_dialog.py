"""LocalCopiesDialog — manage local copies of non-local files opened for external editing."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Label

from nova_navigator.local_copies import CopyEntry, CopyStatus, LocalCopyManager
from nova_widgets import Button, DataTable

from .dialog import DefaultButton, Dialog
from .message_box import MessageBox

_logger = logging.getLogger(__name__)

_ERROR_COLUMN_WIDTH = 16
_LOCAL_COPY_ACTION_GROUP = "local_copy_action"


def _truncate(text: str, width: int) -> str:
    """Truncate *text* to at most *width* characters, marking truncation with an ellipsis."""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


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
            align: left middle;
        }

        #local_copies_actions Button {
            /* width/min-width are set inline in compose_content(), not here: nn.tcss
               has a stray global "Button { width: 100%; }" rule (a leftover from the
               old QuitScreen) that always outranks *any* widget DEFAULT_CSS in
               Textual's cascade -- CSS_PATH ("user") rules beat DEFAULT_CSS
               ("default") rules unconditionally, before specificity or !important
               are even considered. Only an inline style (widget.styles.*) can win. */
            margin: 0 1;
            padding: 0 1;
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
        for button in (self._btn_sync, self._btn_reopen, self._btn_close, self._btn_discard):
            # Inline styles win over nn.tcss's stray global "Button { width: 100%; }" (see
            # the comment on #local_copies_actions Button above) so all four buttons fit.
            button.styles.width = "auto"
            button.styles.min_width = 0
        yield self._table
        yield self._empty_label
        yield Horizontal(self._btn_sync, self._btn_reopen, self._btn_close, self._btn_discard, id="local_copies_actions")

    def on_mount(self) -> None:
        self._table.add_column("File")
        self._table.add_column("Source")
        self._table.add_column("Status")
        self._table.add_column("Error", width=_ERROR_COLUMN_WIDTH)
        self._refresh()
        self.set_interval(1.0, self._refresh)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_button_states()

    @on(Button.Pressed, "#sync_now")
    @work(exclusive=True, group=_LOCAL_COPY_ACTION_GROUP, exit_on_error=False)
    async def _on_sync_now(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        try:
            await self._manager.sync_now(entry)
        except Exception as exc:
            _logger.exception("Sync now failed for %s", entry.copy.source.uri)
            self.notify(f"Sync failed: {exc}", title="Local Copies", severity="error")
        finally:
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
    @work(exclusive=True, group=_LOCAL_COPY_ACTION_GROUP, exit_on_error=False)
    async def _on_close_copy(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        try:
            await self._manager.close(entry)
        except Exception as exc:
            _logger.exception("Close failed for %s", entry.copy.source.uri)
            self.notify(f"Close failed: {exc}", title="Local Copies", severity="error")
        finally:
            self._refresh()

    @on(Button.Pressed, "#discard")
    @work(exclusive=True, group=_LOCAL_COPY_ACTION_GROUP, exit_on_error=False)
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
        try:
            await self._manager.discard(entry)
        except Exception as exc:
            _logger.exception("Discard failed for %s", entry.copy.source.uri)
            self.notify(f"Discard failed: {exc}", title="Local Copies", severity="error")
        finally:
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
            status_text = entry.status.value
            if entry.detector is None and entry.status is not CopyStatus.READ_ONLY:
                status_text += " (closed)"
            self._table.add_row(copy.source.name, copy.source.uri, status_text, _truncate(entry.error or "", _ERROR_COLUMN_WIDTH))
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
