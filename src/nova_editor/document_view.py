"""The per-document state of the editor screen (ADR-4, REQ-15)."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from nova_editor.core.byte_source import ChangeKind
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.timed_text_area import TimedNovaTextArea
from nova_editor.widget import NovaTextArea

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
    """

    editor: NovaTextArea
    """The widget that shows the document."""
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
            editor = editor_class.open(path, id="editor", soft_wrap=soft_wrap, show_line_numbers=show_line_numbers, config=config, timing_file=timing_file)
        except OSError as error:
            state: LoadState = "new" if isinstance(error, FileNotFoundError) else "failed"
            return cls(empty(), path, state), f"Error loading file: {error}"
        return cls(editor, path), None
