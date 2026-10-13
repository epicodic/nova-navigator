"""The per-document state of the editor screen (ADR-4, REQ-15)."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from nova_editor.core.byte_source import ChangeKind
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.languages import language_for_path
from nova_editor.timed_text_area import TimedNovaTextArea
from nova_editor.widget import HexView, NovaTextArea

LoadState = Literal["loaded", "new", "failed"]
"""`loaded`: the editor shows the file; `new`: the file did not exist at start; `failed`: it exists but could not be read."""


def not_regular_reason(path: Path) -> str | None:
    """Return why `path` cannot be opened (it exists but is a FIFO, a device, a directory, ...), or `None`."""
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return None
    if stat.S_ISREG(mode):
        return None
    return f"{path}: not a regular file"


@dataclass
class DocumentView:
    """All state of one open document; the screen owns a reference and nothing else about the document.

    Wrap mode and line numbers are not here: the widget reactives `soft_wrap` and `show_line_numbers` own them.
    The view holds two widgets over the same document: `editor`, the text widget that owns the document and stays mounted for its whole life,
    and `hex_view`, shown instead of it in hex mode; exactly one of them is displayed (`shown`).
    """

    editor: NovaTextArea
    """The text widget: it owns the document (save, reload, the external-change check, search) also while the hex view is shown."""
    file_path: Path | None = None
    """The file the document is bound to (also set when it did not exist or could not be read)."""
    load_state: LoadState = "loaded"
    """How the load ended."""
    needle: str | None = None
    """The last needle searched, repeated by Find Next and Find Previous."""
    last_backward: bool = False
    """Direction of the search in progress or last started (it decides the wrap text)."""
    deferred_change: ChangeKind | None = None
    """A change that `SourceChanged` reported while a save ran: announced after the save when it did not rebase the document."""
    polling: bool = False
    """Whether a check of the file on disk runs on a worker thread."""
    change_question: bool = False
    """Whether a question about a change of the file is open or queued; a second message for the same cause (`SourceChanged`, then `SaveNeedsConfirmation`) must not ask twice."""

    def __post_init__(self) -> None:
        self.hex_view: HexView = HexView(lambda: self.editor.document, id="hex_view")
        """The hex view over the document of `editor` (it follows a replaced document); hidden in text mode. A plain attribute, not a field."""
        self.hex_view.display = False
        self.hex_view.is_read_only = lambda: self.editor.read_only
        self.hex_view.clipboard_owner = lambda: self.editor
        self.hex_view.on_source_changed = self.editor.report_source_changed
        self.hex_view.searching = lambda: self.editor.searching
        self.hex_view.cancel_search = self.editor.cancel_search
        self.needle_hex = False
        """Whether `needle` was typed as hex bytes. A plain attribute, not a field."""
        self._pending = False
        """Whether a scheduled switch has not run its finish step yet; a switch before then carries `_transfer` on instead of reading the stale cursor of the widget it leaves."""
        self._transfer: int | None = None
        """The byte offset that the pending switch puts the cursor on; `None` while it is unknown (the cursor of the target widget is kept). Meaningful only while `_pending`."""
        self._switches = 0
        """Count of the switches; a scheduled finish step that a later switch superseded does nothing."""

    @property
    def hex_mode(self) -> bool:
        """Whether the hex view is shown instead of the text widget."""
        return self.hex_view.display

    @property
    def shown(self) -> NovaTextArea | HexView:
        """The widget the user sees: the target of the focus, the menu actions and the popup position."""
        return self.hex_view if self.hex_mode else self.editor

    def focus_shown(self) -> None:
        """Give the focus to the widget the user sees."""
        self.shown.focus()

    def show_hex(self, on: bool) -> None:
        """Show the hex view (`on`) or the text widget; the cursor follows by byte offset and the selection is not carried (REQ-2).

        Does nothing when the requested view is shown already. A history checkpoint is set first, so that an undo never joins edits across a switch.
        Leaving either view cancels a running search, which would act on a hidden view; leaving the text view also cancels a pending jump.
        When the text cursor has no known byte offset the hex cursor keeps its previous offset.
        The cursor is placed and the focus moved after the next refresh, once the widget that becomes visible has its size.
        A switch before that refresh (two quick toggles) carries the offset of the scheduled one on, also an unknown one (then the cursor of the
        widget shown in the end is kept), and only the last finish step runs.
        The pending state is changed only after every call that may raise, so a failed switch leaves no stale offset behind.
        """
        if on == self.hex_mode:
            return
        editor = self.editor
        hex_view = self.hex_view
        editor.document.history.checkpoint()
        if on:
            editor.cancel_pending()
            if editor.searching:
                editor.cancel_search_now()  # authoritative: a finished search whose completion is queued must not place its match on the hidden view
            offset = self._transfer if self._pending else editor.cursor_byte_offset
        else:
            offset = self._transfer if self._pending else hex_view.cursor.offset
            if editor.searching:
                editor.cancel_search_now()  # authoritative: a finished search whose completion is queued must not place its match on the hidden view
            editor.refresh_if_changed()
        self._switches += 1
        switch = self._switches
        self._pending = True
        self._transfer = offset

        def finish() -> None:
            if switch != self._switches:
                return
            self._pending = False
            self._transfer = None
            if on:
                hex_view.set_cursor(hex_view.cursor.offset if offset is None else offset)  # collapsed, nibble 0 (REQ-2)
                hex_view.scroll_to_cursor(center=True)
                hex_view.focus()
                return
            if offset is not None:
                editor.place_cursor_at_byte(offset)
            editor.focus()

        editor.display = not on
        hex_view.display = on
        editor.call_after_refresh(finish)

    @classmethod
    def open(
        cls,
        path: Path | None,
        *,
        editor_class: type[TimedNovaTextArea],
        soft_wrap: bool,
        show_line_numbers: bool,
        config: LazyConfig | None,
        timing_file: str | None,
    ) -> tuple[DocumentView, str | None]:
        """Open `path`, or an empty document for `None`.

        Returns:
            The view and an error text for the user (`None` when the load worked). When the file cannot be opened the view holds an empty editor and the matching load state.
        """

        def empty() -> TimedNovaTextArea:
            return editor_class(id="editor", text="", soft_wrap=soft_wrap, show_line_numbers=show_line_numbers, timing_file=timing_file)

        if path is None:
            return cls(empty()), None
        reason = not_regular_reason(path)  # opening a FIFO would block
        if reason is not None:
            return cls(empty(), path, "failed"), f"Error loading file: {reason}"
        try:
            editor = editor_class.open(path, language=language_for_path(path), id="editor", soft_wrap=soft_wrap, show_line_numbers=show_line_numbers, config=config, timing_file=timing_file)
        except OSError as error:
            state: LoadState = "new" if isinstance(error, FileNotFoundError) else "failed"
            return cls(empty(), path, state), f"Error loading file: {error}"
        return cls(editor, path), None
