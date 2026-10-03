"""Status line of `nova_edit`: cursor, byte offset, line count with indexing state, line ending and modified state (REQ-16)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Literal

from textual.timer import Timer
from textual.widgets import Static

SEPARATOR = "  "


@dataclass(frozen=True)
class StatusState:
    """What the status line shows; built from the editor by the app, formatted by `format_status`."""

    line: int
    """1-based line of the cursor."""
    column: int
    """1-based column of the cursor (characters)."""
    column_kind: Literal["exact", "provisional", "pending"]
    byte_offset: int | None
    line_count: int
    line_count_exact: bool
    indexing_percent: int
    line_ending: Literal["LF", "CRLF", "CR"]
    modified: bool
    new_file: bool
    wrap: bool
    goto_percent: int | None
    """Percent of a goto that waits for the scan, `None` when no goto is pending."""


def format_status(state: StatusState, width: int) -> str:
    """Return the status text; parts are added in priority order while the text fits in `width` (the first part is always kept)."""
    column = {"exact": f"Col {state.column}", "provisional": f"Col ~{state.column}", "pending": "Col ..."}[state.column_kind]
    parts: list[str] = []
    if state.goto_percent is not None:
        parts.append(f"Goto {state.goto_percent}%  Esc cancels")
    parts.append(f"Ln {state.line:,}{SEPARATOR}{column}")
    parts.append("Byte ?" if state.byte_offset is None else f"Byte {state.byte_offset:,}")
    noun = "line" if state.line_count == 1 else "lines"
    if state.line_count_exact:
        parts.append(f"{state.line_count:,} {noun}")
    else:
        parts.append(f">= {state.line_count:,} {noun} (indexing {state.indexing_percent}%)")
    parts.append(state.line_ending)
    if state.modified:
        parts.append("Modified")
    if state.new_file:
        parts.append("New file")
    parts.append("Wrap" if state.wrap else "No wrap")
    text = parts[0]
    for part in parts[1:]:
        candidate = f"{text}{SEPARATOR}{part}"
        if len(candidate) > width:
            break
        text = candidate
    return text


class StatusLine(Static):
    """One row below the editor; `request()` coalesces updates to at most one per `REFRESH_SECONDS` and never does work on the key's critical path."""

    REFRESH_SECONDS: ClassVar[float] = 0.05

    def __init__(self, source: Callable[[], StatusState | None]) -> None:
        super().__init__("", id="status_line", markup=False)
        self.text = ""
        """The text shown."""
        self.flushes = 0
        """Number of times the state was read and the text recomputed (the benchmark reads it)."""
        self._source = source
        self._dirty = False
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        self.request()

    def on_resize(self) -> None:
        """The text is fitted to the width, so a new size needs a new text (and the first layout replaces a text cut to one cell)."""
        self.request()

    def request(self) -> None:
        """Ask for a refresh: set the dirty flag and start the timer when none is pending."""
        self._dirty = True
        if self._timer is None:
            self._timer = self.set_timer(self.REFRESH_SECONDS, self._flush)

    def _flush(self) -> None:
        self._timer = None
        self._dirty = False
        self.flushes += 1
        state = self._source()
        text = "" if state is None else format_status(state, max(self.size.width, 1))
        if text != self.text:
            self.text = text
            self.update(text)
