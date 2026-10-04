"""Synthetic file builder and oracle functions for lazy view tests.

This module provides:
- make_mixed: creates synthetic test files with various text features
- oracle functions: independently verify row content, display widths, and ranges
"""

from __future__ import annotations

import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar, NamedTuple

from rich.cells import cell_len
from textual.app import App, ComposeResult
from textual.message import Message
from textual.pilot import Pilot
from textual.widget import Widget

from nova_editor.core import ByteSource, PreadSource
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.document._lazy_wrapped_document import LazyWrappedDocument
from nova_editor.timed_text_area import TimedNovaTextArea
from nova_editor.widget import NovaTextArea

# Lowered options for testing lazy document with small synthetic files
LOWERED_OPTIONS = {
    "stride": 4,
    "index_long_line_threshold": 64,
    "long_row_threshold": 512,
    "word_wrap_limit": 128,
    "checkpoint_chars": 64,
}


_INVALID_BYTE_TABLE = str.maketrans(dict.fromkeys(range(0xDC80, 0xDD00), 0xFFFD))


class RowRange(NamedTuple):
    """Byte range and content boundaries for a single row."""

    start: int
    content_end: int
    end: int


def make_mixed(path: Path, *, terminator: bytes = b"\n", long_chars: int = 2000) -> Path:
    r"""Write synthetic test file with various text features.

    Args:
        path: file path to write to
        terminator: line terminator (b"\n", b"\r\n", or b"\r")
        long_chars: length of the long row

    Returns:
        The path argument, for chaining.

    Writes:
    - 10 short ASCII rows
    - a row with tabs, CJK (日本語), combining marks (é), and emoji (😀)
    - a medium row of 300 characters
    - a long row cycling "abc\tä" with one \xff byte in the middle
    - an empty row
    - a final row without terminator
    """
    rows: list[bytes] = [f"short line {i}".encode() for i in range(10)]

    # Row with tabs, CJK, combining marks, emoji
    mixed_text = "\t\ttabbed\t\tline with 日本語 and é and 😀"
    rows.append(mixed_text.encode())

    # Medium row of 300 characters
    medium_row = ("word " * 60)[:300]  # 300 chars exactly
    rows.append(medium_row.encode())

    # Long row cycling through pattern with invalid byte
    pattern = "abc\t日é😀"
    long_row_bytes = bytearray()
    char_count = 0
    midpoint_marked = False

    while len(long_row_bytes) < long_chars:
        # Mark the midpoint for 0xFF insertion
        if not midpoint_marked and len(long_row_bytes) >= long_chars // 2:
            if len(long_row_bytes) + 1 < long_chars:  # Ensure room for 0xFF and more
                long_row_bytes.append(0xFF)
                midpoint_marked = True
            else:
                # Reached end before inserting 0xFF; insert it anyway
                long_row_bytes.append(0xFF)
                break

        # Add one character from pattern (as bytes)
        char = pattern[char_count % len(pattern)]
        char_bytes = char.encode("utf-8")

        # Check if adding this character would exceed long_chars
        if len(long_row_bytes) + len(char_bytes) > long_chars:
            break

        long_row_bytes.extend(char_bytes)
        char_count += 1

    rows.append(bytes(long_row_bytes))

    # Empty row
    rows.append(b"")

    # Final row without terminator (added separately below)
    final_row = b"final line"

    # Write file with terminators between rows but not after the final row
    file_bytes = terminator.join(rows) + (terminator if rows else b"") + final_row

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(file_bytes)
    return path


def oracle_row_ranges(data: bytes) -> list[RowRange]:
    r"""Parse row boundaries in data with any terminator variant.

    Returns:
        List of (start, content_end, end) tuples for each row, where:
        - start: byte offset of row start
        - content_end: byte offset before the terminator
        - end: byte offset after the terminator (or end of file)

    Detects and handles terminators: LF (\n), CRLF (\r\n), CR (\r).
    """
    if not data:
        return []

    ranges: list[RowRange] = []
    pos = 0

    while pos < len(data):
        start = pos
        content_end = pos

        # Find next terminator
        while content_end < len(data):
            if content_end + 1 < len(data) and data[content_end : content_end + 2] == b"\r\n":
                # CRLF terminator
                end = content_end + 2
                break
            if data[content_end] in (ord(b"\n"), ord(b"\r")):
                # LF or CR terminator
                end = content_end + 1
                break
            content_end += 1
        else:
            # No terminator found; rest is final row
            end = len(data)

        ranges.append(RowRange(start, content_end, end))
        pos = end

    return ranges


def oracle_row_text(data: bytes, row_index: int, _terminator_regex: bytes = rb"\r\n|\n|\r") -> str:
    """Return text of specified row, decoded with surrogateescape.

    Args:
        data: file bytes
        row_index: which row to extract (0-indexed)
        _terminator_regex: unused parameter for compatibility; detection is automatic

    Returns:
        Row text decoded as UTF-8 with surrogateescape error handling.

    Raises:
        IndexError: if row_index is out of range.
    """
    ranges = oracle_row_ranges(data)
    if row_index >= len(ranges):
        raise IndexError(f"row index {row_index} out of range (file has {len(ranges)} rows)")

    row_range = ranges[row_index]
    row_bytes = data[row_range.start : row_range.content_end]
    return row_bytes.decode("utf-8", errors="surrogateescape")


def oracle_display_column(text: str, column: int, tab_width: int = 4) -> int:
    """Return the display column of a character column, accounting for tabs and wide characters.

    Args:
        text: row text (lone surrogates from surrogateescape count as one cell)
        column: character column (0-indexed) within the row; values beyond the text count the whole text
        tab_width: tab stop width in cells

    Returns:
        Display column in cells at which the character with index `column` starts.

    The calculation:
    - Iterates the first `column` characters
    - For tabs: advance to the next multiple of tab_width
    - For other chars: use rich.cells.cell_len for display width
    """
    display_col = 0
    for char in text[: max(0, column)]:
        if char == "\t":
            display_col += tab_width - (display_col % tab_width)
        else:
            display_col += cell_len(char)
    return display_col


def oracle_cells(text: str, tab_width: int = 4) -> list[str]:
    """Return the display cells of `text`: one entry per cell, a wide character is followed by an empty continuation cell.

    A zero-width character is appended to the cell of the character before it (dropped at the start of the text).
    """
    cells: list[str] = []
    for char in text.translate(_INVALID_BYTE_TABLE):  # the widget shows an escaped invalid byte as U+FFFD
        if char == "\t":
            cells.extend(" " * (tab_width - len(cells) % tab_width))
            continue
        cells_wide = cell_len(char)
        if cells_wide == 0:
            if cells:
                previous = len(cells) - 1
                while previous > 0 and cells[previous] == "":
                    previous -= 1
                cells[previous] += char
            continue
        cells.append(char)
        cells.extend([""] * (cells_wide - 1))
    return cells


def cells_to_text(cells: list[str], start: int, width: int) -> str:
    """Join `width` cells from `start` like a cropped strip: a wide character cut by either edge becomes spaces; padded to `width`."""
    picked = cells[start : start + width]
    if picked and picked[0] == "" and start > 0:
        picked[0] = " "
    if picked and start + len(picked) < len(cells) and cells[start + len(picked)] == "" and picked[-1] != "":
        picked[-1] = " "
    text = "".join(picked)
    return text + " " * (width - len(picked))


def oracle_window(data: bytes, row: int, x: int, width: int, tab_width: int = 4) -> str:
    """Return the text of the display cells [x, x + width) of a row, padded with spaces to `width`."""
    cells = oracle_cells(oracle_row_text(data, row), tab_width)
    return cells_to_text(cells, x, width)


def oracle_section(data: bytes, row: int, section: int, grid_width: int, view_width: int, tab_width: int = 4) -> str:
    """Return the grid-wrapped section `section` of a row as displayed: first `view_width` cells from its first character.

    Section k holds the characters whose display start lies in [k*grid_width, (k+1)*grid_width); tab phase is the true display column.
    """
    display = 0
    members: list[str] = []
    first_disp: int | None = None
    for char in oracle_row_text(data, row):
        if section * grid_width <= display < (section + 1) * grid_width:
            if first_disp is None:
                first_disp = display
            members.append(char)
        display += tab_width - display % tab_width if char == "\t" else cell_len(char)
    if first_disp is None:
        return " " * view_width
    phantom = first_disp % tab_width
    cells = oracle_cells(" " * phantom + "".join(members), tab_width)
    return cells_to_text(cells, phantom, view_width)


def wait_frontier(document: LazyDocument, row: int, timeout: float = 10.0) -> None:
    """Wait until the line index is complete and the long index of `row` has scanned the whole row."""
    assert document.wait_indexed(timeout)
    assert document.long_index(row).join(timeout)


class HostApp(App[None]):
    """Minimal app that hosts one widget and counts `SourceChanged` messages."""

    def __init__(self, widget: Widget) -> None:
        super().__init__()
        self._widget = widget
        self.source_changed: list[str] = []
        self.messages: list[Message] = []
        """Index and jump messages of the widget, in arrival order."""

    def compose(self) -> ComposeResult:
        yield self._widget

    def on_nova_text_area_source_changed(self, message: NovaTextArea.SourceChanged) -> None:
        self.source_changed.append(message.reason)

    def on_nova_text_area_index_progress(self, message: NovaTextArea.IndexProgress) -> None:
        self.messages.append(message)

    def on_nova_text_area_indexing_complete(self, message: NovaTextArea.IndexingComplete) -> None:
        self.messages.append(message)

    def on_nova_text_area_jump_progress(self, message: NovaTextArea.JumpProgress) -> None:
        self.messages.append(message)

    def on_nova_text_area_jump_completed(self, message: NovaTextArea.JumpCompleted) -> None:
        self.messages.append(message)

    def on_nova_text_area_jump_rejected(self, message: NovaTextArea.JumpRejected) -> None:
        self.messages.append(message)

    def names(self) -> list[str]:
        """Return the class names of the recorded jump and index messages, in arrival order."""
        return [type(message).__name__ for message in self.messages]


async def wait_until(pilot: Pilot[None], condition: Callable[[], bool], limit: float = 10.0) -> None:
    """Let the app run until `condition()` holds; fail after `limit` seconds."""
    deadline = time.monotonic() + limit
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        await pilot.pause(0.02)


MIN_TEXT_CELLS = 5
"""Fewest cells of text a settled first layout shows."""


async def await_first_layout(pilot: Pilot[None], area: NovaTextArea, limit: float = 10.0) -> None:
    """Wait until the widget has its first layout: a non-empty region and a first row that renders more than a single character."""

    def laid_out() -> bool:
        if area.region.width <= 0 or area.region.height <= 0 or area.scrollable_content_region.width - area.gutter_width < MIN_TEXT_CELLS:
            return False
        return len("".join(segment.text for segment in area.render_line(0)).strip()) >= MIN_TEXT_CELLS  # a transient first paint shows a single character

    await wait_until(pilot, laid_out, limit)


def lazy_wrapped(area: NovaTextArea) -> LazyWrappedDocument:
    """Return the wrapped view of a lazily opened widget."""
    wrapped = area.wrapped_document
    assert isinstance(wrapped, LazyWrappedDocument)
    return wrapped


def row_strip_text(area: NovaTextArea, row: int, width: int | None = None, section: int = 0) -> str:
    """Return the text of the first `width` cells (default: the visible text width) of the screen line that shows `section` of `row`."""
    y = lazy_wrapped(area).y_of_row(row) + section - area.scroll_offset.y
    strip = area.render_line(y).crop(area.gutter_width, area.gutter_width + (width or (area.scrollable_content_region.width - area.gutter_width)))
    return strip.text


class GateSource:
    """`ByteSource` whose scan reads (`cache=False`) block while the gate is closed."""

    def __init__(self, path: Path) -> None:
        self._inner = PreadSource(path)
        self.gate = threading.Event()
        self.gate.set()

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if not cache:
            assert self.gate.wait(30)
        return self._inner.read(offset, size, cache=cache)

    def close(self) -> None:
        self._inner.close()


class TrickleSource(GateSource):
    """`GateSource` for a partly scanned long row: the long-row scan gets `budget` reads of at most `block` bytes, then it is held back.

    The line scan is never held. `release()` lets the long-row scan run to the end at full speed.
    """

    def __init__(self, path: Path, budget: int = 2, block: int = 512) -> None:
        super().__init__(path)
        self._block = block
        self._budget: int | None = budget
        self._reads = 0
        self._lock = threading.Lock()
        self.gate.clear()
        self.blocked = threading.Event()
        """Set when a long-row scan read is held back: the frontier is provably behind."""

    def release(self) -> None:
        """Let the long-row scan run to the end at full speed."""
        with self._lock:
            self._budget = None
        self.gate.set()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if not cache and threading.current_thread().name == "long-line-scan":
            with self._lock:
                armed = self._budget is not None
                hold = armed and self._reads >= (self._budget or 0)
                self._reads += 1
            if armed:
                size = min(size, self._block)
            if hold:
                self.blocked.set()
                assert self.gate.wait(30)
        return self._inner.read(offset, size, cache=cache)


class GatedLineSource:
    """`ByteSource` for a line scan that spans many blocks: scan reads are short (`block` bytes) and can be slowed or held back.

    Scan reads (`cache=False` on the `line-index-scan` thread) return at most `block` bytes and sleep `delay` seconds first.
    Once the scan reached byte `threshold`, the next scan read blocks until `release()`. Other reads are never held back.
    """

    def __init__(self, path: Path, *, threshold: int | None = None, block: int = 256, delay: float = 0.0) -> None:
        self._inner = PreadSource(path)
        self._threshold = threshold
        self._block = block
        self._delay = delay
        self.gate = threading.Event()
        if threshold is None:
            self.gate.set()
        self.blocked = threading.Event()
        """Set when a scan read is held back: the frontier is provably behind."""
        self.reads = 0

    def release(self) -> None:
        """Let the line scan run to the end at full speed."""
        self._threshold = None
        self._delay = 0.0
        self.gate.set()

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if not cache and threading.current_thread().name == "line-index-scan":
            self.reads += 1
            threshold = self._threshold
            if threshold is not None and offset >= threshold:
                self.blocked.set()
                assert self.gate.wait(30)
            if self._delay:
                time.sleep(self._delay)
            size = min(size, self._block)
        return self._inner.read(offset, size, cache=cache)

    def close(self) -> None:
        self._inner.close()


_GATES: weakref.WeakKeyDictionary[NovaTextArea, GatedLineSource] = weakref.WeakKeyDictionary()


def open_with_gated_line_scan(
    tmp_path: Path,
    rows: int = 3000,
    *,
    threshold: int | None = 2048,
    delay: float = 0.0,
    config: LazyConfig | None = None,
    show_line_numbers: bool = False,
) -> tuple[NovaTextArea, Path]:
    """Open a file of `rows` short rows ("row N") whose line scan is held back at byte `threshold` until `release_line_scan(area)`.

    Args:
        tmp_path: directory that receives the file.
        rows: number of rows of the file.
        threshold: byte offset where the scan is held back; `None` never holds it back (use `delay` to slow it).
        delay: pause of the scan per chunk.
        config: lazy config of the widget; defaults to the lowered options.
        show_line_numbers: show the gutter from the start.

    Returns:
        The widget (not mounted yet) and the file path.
    """
    path = tmp_path / "rows.txt"
    path.write_bytes("".join(f"row {i}\n" for i in range(rows)).encode())
    source = GatedLineSource(path, threshold=threshold, delay=delay)
    area = NovaTextArea.open(
        source,
        config=replace(config or LazyConfig(**LOWERED_OPTIONS), sync_scan_limit=0),
        show_line_numbers=show_line_numbers,
    )  # a gated scan needs the background thread
    _GATES[area] = source
    return area, path


def gated_source(area: NovaTextArea) -> GatedLineSource:
    """Return the gated source behind a widget opened by `open_with_gated_line_scan`."""
    return _GATES[area]


def release_line_scan(area: NovaTextArea) -> None:
    """Let the held-back line scan of `area` run to the end."""
    _GATES[area].release()


class GatedRowEditor(TimedNovaTextArea):
    """Opens its file through a source whose line scan is held back at byte `threshold` (default 0: row 0 is not resolved yet)."""

    gates: ClassVar[list[GatedLineSource]] = []
    """The gate of every file opened, in order; a test clears the list first."""
    threshold: ClassVar[int] = 0
    """Byte at which the line scan stops until `release()`."""

    @classmethod
    def open(
        cls,
        source: Path | str | ByteSource,
        *,
        language: str | None = None,
        soft_wrap: bool = False,
        config: LazyConfig | None = None,
        highlight_limit: int = 1_048_576,
        timing_file: str | None = None,
        **kwargs: Any,
    ) -> TimedNovaTextArea:
        assert isinstance(source, Path)
        gate = GatedLineSource(source, threshold=cls.threshold)
        cls.gates.append(gate)
        lowered = replace(config or LazyConfig(**LOWERED_OPTIONS), sync_scan_limit=0)
        return super().open(gate, language=language, soft_wrap=soft_wrap, config=lowered, highlight_limit=highlight_limit, timing_file=timing_file, **kwargs)


class GatedRowsEditor(GatedRowEditor):
    """A `GatedRowEditor` whose scan stops after 2 KiB: the first rows are resolved, the rest is not."""

    threshold = 2048
