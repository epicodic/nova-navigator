"""Status line of `nova_edit`: file name, cursor, byte offset with percentage, wrap mode, line count with indexing state, line ending and modified state (REQ-9, REQ-10, REQ-16)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import ClassVar, Literal

from textual.timer import Timer
from textual.widgets import Static

from .core.control_names import bracketed_control_name
from .widget.hex_layout import CHAR_TABLE

SEPARATOR = "  "
UNSAVED_NAME = "(unsaved)"
"""Shown in place of the file name while the document is not bound to a path."""
ELLIPSIS = "…"
"""Replaces the start of a file name that is longer than half of the status line."""


@dataclass(frozen=True)
class StatusState:
    """What the status line shows; built from the editor by the app, formatted by `format_status`."""

    file_name: str | None
    """Name (no directory) of the file the document is bound to, `None` while it has no path."""
    line: int | None
    """1-based line of the cursor, `None` while the line index has not resolved that row yet."""
    column: int
    """1-based column of the cursor (characters)."""
    column_kind: Literal["exact", "provisional", "pending"]
    byte_offset: int | None
    byte_percent: int | None
    """Whole percent of the file that lies before the cursor, `None` when the byte offset or the size is unknown."""
    line_count: int
    line_count_exact: bool
    indexing_percent: int
    line_ending: Literal["LF", "CRLF", "CR"]
    modified: bool
    new_file: bool
    wrap: bool
    goto_percent: int | None
    """Percent of a goto that waits for the scan, `None` when no goto is pending."""
    note: str | None = None
    """A transient text (progress or result of a save, a search or a reload) shown right after the file name; set by `StatusLine.set_note`."""


@dataclass(frozen=True)
class HexStatusState:
    """What the status line shows in the hex view; built from the view by the screen, formatted by `format_hex_status` (REQ-19)."""

    file_name: str | None
    """Name (no directory) of the file the document is bound to, `None` while it has no path."""
    offset: int
    """Offset of the cursor, `0..length`."""
    length: int
    """Length of the document in bytes."""
    byte: int | None
    """The byte under the cursor; `None` at the end position or when it cannot be read."""
    insert: bool
    """Whether typing inserts (`INS`) rather than overwrites (`OVR`)."""
    modified: bool
    """Whether the document has unsaved changes."""
    note: str | None = None
    """A transient text shown right after the file name; set by `StatusLine.set_note`."""


def row_is_known(row: int, line_count: int, complete: bool) -> bool:
    """Whether the 0-based `row` of the cursor is resolved by the line scan.

    While the scan runs, `line_count` is a lower bound and its last row is the open row that the scan has not terminated yet (`LineIndex.row_range`).
    """
    return complete or row < line_count - 1


_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")
_KIB = 1024


def format_sizes(done: int, total: int) -> str:
    """Format `done / total` in the binary unit that suits `total` (`1.2 / 5.0 GiB`)."""
    unit = 0
    while unit < len(_UNITS) - 1 and total >= _KIB ** (unit + 1):
        unit += 1
    scale = _KIB**unit
    if unit == 0:
        return f"{done} / {total} B"
    return f"{done / scale:.1f} / {total / scale:.1f} {_UNITS[unit]}"


def save_progress_text(phase: str, done: int, total: int, *, started: bool = False) -> str:
    """The note of a running save: the bytes written, or the name of a later phase; `started` (an in-place save wrote its first byte) drops the hint that Esc cancels."""
    if phase == "flushing":
        return "Flushing"
    if phase == "history":
        return "Preserving undo history"
    if phase == "finishing":
        return "Finishing"
    percent = 100 if total <= 0 else round(done * 100 / total)
    hint = "" if started else "  Esc cancels"
    return f"Saving  {format_sizes(done, total)}  {percent} %{hint}"


def byte_percent(byte_offset: int | None, length: int) -> int | None:
    """Return the whole percent of `length` bytes that lie before `byte_offset`, or `None` when the offset is unknown or the document is empty."""
    if byte_offset is None or length <= 0:
        return None
    return min(100, byte_offset * 100 // length)


def _fit_name(name: str, width: int) -> str:
    """Return `name`, or its end behind an ellipsis when it is longer than half of `width`."""
    limit = max(1, width // 2)
    if len(name) <= limit:
        return name
    return ELLIPSIS + name[len(name) - (limit - 1) :] if limit > 1 else ELLIPSIS


def format_status(state: StatusState, width: int) -> str:
    """Return the status text; parts are added in priority order while the text fits in `width` (the file name is always kept)."""
    column = {"exact": f"Col {state.column}", "provisional": f"Col ~{state.column}", "pending": "Col ..."}[state.column_kind]
    parts: list[str] = [_fit_name(UNSAVED_NAME if state.file_name is None else state.file_name, width)]
    if state.note is not None:
        room = width - len(parts[0]) - len(SEPARATOR)
        if room > 0:
            parts.append(state.note if len(state.note) <= room else state.note[: room - 1] + ELLIPSIS)
    if state.goto_percent is not None:
        parts.append(f"Goto {state.goto_percent}%  Esc cancels")
    line = "?" if state.line is None else f"{state.line:,}"
    parts.append(f"Ln {line}{SEPARATOR}{column}")
    if state.byte_offset is None:
        parts.append("Byte ?")
    else:
        percent = "" if state.byte_percent is None else f" ({state.byte_percent}%)"
        parts.append(f"Byte {state.byte_offset:,}{percent}")
    if state.modified:
        parts.append("Modified")
    if state.new_file:
        parts.append("New file")
    parts.append("Wrap" if state.wrap else "No wrap")
    noun = "line" if state.line_count == 1 else "lines"
    if state.line_count_exact:
        parts.append(f"{state.line_count:,} {noun}")
    else:
        parts.append(f">= {state.line_count:,} {noun} (indexing {state.indexing_percent}%)")
    parts.append(state.line_ending)
    text = parts[0]
    for part in parts[1:]:
        candidate = f"{text}{SEPARATOR}{part}"
        if len(candidate) > width:
            break
        text = candidate
    return text


def format_size(length: int) -> str:
    """Format a byte count: `N B` below 1 KiB, else one decimal and the binary unit (`64.5 KiB`)."""
    unit = 0
    while unit < len(_UNITS) - 1 and length >= _KIB ** (unit + 1):
        unit += 1
    if unit == 0:
        return f"{length} B"
    return f"{length / _KIB**unit:.1f} {_UNITS[unit]}"


HEX_DROP_ORDER: tuple[str, ...] = ("byte", "offset", "hex", "size", "modified", "mode")
"""The order in which hex status parts are dropped at a narrow width (DEC-27).

The position parts go first (the byte value, then the offset), then the rest tail-first; the name and the note are never dropped.
"""


def _hex_byte_text(state: HexStatusState) -> str:
    if state.offset >= state.length:
        return "EOF"
    if state.byte is None:
        return "?"
    byte = state.byte
    label = bracketed_control_name(byte)
    if label is None:
        label = f"'{CHAR_TABLE[byte]}'"
    return f"0x{byte:02X} {byte} {label}"


def format_hex_status(state: HexStatusState, width: int) -> str:
    """Return the hex status text: name, note, offset, byte value, `OVR`/`INS`, `Modified`, size and `Hex`.

    The parts keep this display order; when they do not fit in `width`, parts are dropped in the order of `HEX_DROP_ORDER` until the text fits.
    The file name is always kept; a note that does not fit is cut with an ellipsis, never dropped.
    """
    head: list[str] = [_fit_name(UNSAVED_NAME if state.file_name is None else state.file_name, width)]
    if state.note is not None:
        room = width - len(head[0]) - len(SEPARATOR)
        if room > 0:
            head.append(state.note if len(state.note) <= room else state.note[: room - 1] + ELLIPSIS)
    digits = max(8, len(f"{state.length:X}"))
    percent = byte_percent(state.offset, state.length)
    parts = {
        "offset": f"Off 0x{state.offset:0{digits}X} ({state.offset})" + ("" if percent is None else f" {percent}%"),
        "byte": _hex_byte_text(state),
        "mode": "INS" if state.insert else "OVR",
        "modified": "Modified",
        "size": format_size(state.length),
        "hex": "Hex",
    }
    shown = ["offset", "byte", "mode", "modified", "size", "hex"]
    if not state.modified:
        shown.remove("modified")
    drop = [key for key in HEX_DROP_ORDER if key in shown]
    while True:
        text = SEPARATOR.join(head + [parts[key] for key in shown])
        if len(text) <= width or not drop:
            return text
        shown.remove(drop.pop(0))


class StatusLine(Static):
    """One row below the editor; `request()` coalesces updates to at most one per `REFRESH_SECONDS` and never does work on the key's critical path."""

    REFRESH_SECONDS: ClassVar[float] = 0.05
    NOTE_SECONDS: ClassVar[float] = 4.0
    """How long a timed note stays (a result such as `Saved`)."""

    def __init__(self, source: Callable[[], StatusState | HexStatusState | None]) -> None:
        super().__init__("", id="status_line", markup=False)
        self.text = ""
        """The text shown."""
        self.flushes = 0
        """Number of times the state was read and the text recomputed (the benchmark reads it)."""
        self._source = source
        self._dirty = False
        self._timer: Timer | None = None
        self.note: str | None = None
        """The note shown after the file name, `None` when there is none."""
        self._note_timer: Timer | None = None

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

    def set_note(self, text: str | None, *, timed: bool = False) -> None:
        """Show `text` right after the file name (`None` clears it); a timed note clears itself after `NOTE_SECONDS`."""
        if self._note_timer is not None:
            self._note_timer.stop()
            self._note_timer = None
        self.note = text
        if text is not None and timed:
            self._note_timer = self.set_timer(self.NOTE_SECONDS, self.clear_note)
        self.request()

    def clear_note(self) -> None:
        """Remove the note."""
        self.set_note(None)

    def _flush(self) -> None:
        self._timer = None
        self._dirty = False
        self.flushes += 1
        state = self._source()
        if state is not None and self.note is not None:
            state = replace(state, note=self.note)
        width = max(self.size.width, 1)
        if state is None:
            text = ""
        elif isinstance(state, HexStatusState):
            text = format_hex_status(state, width)
        else:
            text = format_status(state, width)
        if text != self.text:
            self.text = text
            self.update(text)
