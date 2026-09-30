"""Read-only `DocumentBase` over the core indexes: rows are decoded on demand, long rows only through windows (ACT3 design 4.1 to 4.3).

The document layer is the only one that touches `nova_editor.core`. No call waits for a scan: whatever is not scanned yet
is reported as `None` (or an empty slice) and the caller asks again after a subscriber notification.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple, overload

from textual.geometry import Size

from nova_editor.core import ByteSource, LineIndex, LineSnapshot, LongLineIndex, PreadSource, RowRange
from nova_editor.core.text_width import SURROGATE_ESCAPE, advance_disp, locate_cover, utf8_len
from nova_editor.document._document import DocumentBase, EditResult, Location, Newline
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._long_row_anchor import LongRowAnchorIndex

if TYPE_CHECKING:
    from tree_sitter import Node, Query

MAX_WINDOW_CHARS = 8192
"""The most characters any capability call decodes from a long row."""
MAX_SLICE_ROWS = 128
"""The most rows one `__getitem__` slice or `get_text_range` call touches."""
_RANGE_CACHE_ROWS = 4096
_MAX_RETIRED = 64
_MAX_EVENTS = 10_000

RowClass = Literal["short", "medium", "long"]


class WholeLineAccess(AssertionError):
    """A caller asked for the whole text of a long row (or of the whole document)."""


class RowUnavailable(RuntimeError):
    """The row exists but its byte range is not resolvable yet: the scan has not reached its end, or the read budget was exceeded."""


class CallRecord(NamedTuple):
    """One recorded document call."""

    method: str
    row: int | None
    decoded_chars: int


class CallLog:
    """Thread-safe record of document calls with the number of characters each one decoded."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[CallRecord] = []
        self.refusals: list[tuple[str, int | None]] = []
        self._max = 0

    def record(self, method: str, row: int | None, decoded_chars: int) -> None:
        """Record one call; only the first 10,000 events are kept, the maximum covers all of them."""
        with self._lock:
            self._max = max(self._max, decoded_chars)
            if len(self.events) < _MAX_EVENTS:
                self.events.append(CallRecord(method, row, decoded_chars))

    def refuse(self, method: str, row: int | None) -> None:
        """Record a refused whole-line or whole-document access."""
        with self._lock:
            if len(self.refusals) < _MAX_EVENTS:
                self.refusals.append((method, row))

    @property
    def max_decoded_chars(self) -> int:
        """The largest number of characters decoded by a single recorded call."""
        with self._lock:
            return self._max


def index_step(config: LazyConfig) -> int:
    """Return the effective checkpoint spacing of the long indexes of a document with `config`."""
    return max(1, min(config.checkpoint_chars, MAX_WINDOW_CHARS))


class LazyDocument(DocumentBase):
    """Read-only document backed by a `ByteSource`, a `LineIndex` and one `LongLineIndex` per displayed long row.

    Capability methods on a long row never decode more than `MAX_WINDOW_CHARS` characters and never wait for a scan; they
    return `None` (or `""` / `False`) beyond the scanned frontier. The effective checkpoint spacing of a long index is
    `min(config.checkpoint_chars, MAX_WINDOW_CHARS)` so that every query stays inside that bound.
    `SourceChanged` raised by the core propagates to the caller.
    """

    def __init__(self, source: ByteSource, config: LazyConfig | None = None, *, autostart: bool = True) -> None:
        self._config = config or LazyConfig()
        self._source = source
        self._lock = threading.RLock()
        self._closed = False
        self._started = False
        self._line_index = LineIndex(
            source,
            stride=self._config.stride,
            long_line_threshold=self._config.index_long_line_threshold,
            yield_seconds=self._config.yield_seconds,
        )
        self._ranges: OrderedDict[int, RowRange] = OrderedDict()
        self._texts: OrderedDict[int, str] = OrderedDict()
        self._long: OrderedDict[int, LongLineIndex] = OrderedDict()
        self._retired: list[LongLineIndex] = []
        self._subscribers: list[Callable[[], None]] = []
        self._newline: Newline | None = None
        self._seen_width = 0
        self.call_log = CallLog()
        if autostart:
            self.start_scan()

    @classmethod
    def from_path(cls, path: Path | str, config: LazyConfig | None = None, *, autostart: bool = True) -> LazyDocument:
        """Open `path` through a `PreadSource` that the document owns and closes."""
        return cls(PreadSource(path), config, autostart=autostart)

    # -- lifecycle ----------------------------------------------------------------------------
    @property
    def tab_width(self) -> int:
        """The tab width the long-row indexes use (`LazyConfig.tab_width`); the widget sets it from its `indent_width` at `open`."""
        return self._config.tab_width

    def start_scan(self) -> None:
        """Start the background line scan; a second call does nothing."""
        with self._lock:
            if self._started:
                return
            self._started = True
        self._line_index.start()

    def wait_indexed(self, timeout: float) -> bool:
        """Wait for the line scan to complete (tests and workers only); return whether it completed."""
        return self._line_index.join(timeout)

    def close(self) -> None:
        """Cancel and join every scan, then close the source; a second call does nothing."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            indexes = [*self._long.values(), *self._retired]
            self._long.clear()
            self._retired.clear()
        self._line_index.cancel()
        for index in indexes:
            index.cancel()
        for index in indexes:
            index.join()
        self._line_index.join()
        self._source.close()

    def subscribe(self, callback: Callable[[], None]) -> None:
        """Call `callback` (on a scan thread) on progress of the line index and of every long index, including later ones."""
        with self._lock:
            self._subscribers.append(callback)
            existing = list(self._long.values())
        self._line_index.subscribe(callback)
        for index in existing:
            index.subscribe(callback)

    def is_growing(self) -> bool:
        """Whether the line scan or a long-row scan is still running, i.e. sizes and estimates can still change."""
        with self._lock:
            if self._closed:
                return False
            indexes = list(self._long.values())
        snap = self._line_index.snapshot()
        if not snap.complete and snap.error is None:
            return True
        return any(index.running for index in indexes)

    def snapshot(self) -> LineSnapshot:
        """Return the consistent state of the line scan."""
        return self._line_index.snapshot()

    # -- row bookkeeping ----------------------------------------------------------------------
    def _norm(self, row: int) -> int:
        return row + self.line_count if row < 0 else row

    def _range(self, row: int) -> RowRange:
        """Return the byte range of `row`: `IndexError` beyond the scanned count, `RowUnavailable` when it cannot be resolved yet."""
        row = self._norm(row)
        if row < 0:
            raise IndexError(row)
        with self._lock:
            cached = self._ranges.get(row)
            if cached is not None:
                self._ranges.move_to_end(row)
                return cached
        found = self._line_index.row_range(row)
        if found is None:
            snap = self._line_index.snapshot()
            if snap.error is not None:
                raise snap.error
            if row >= snap.count:
                raise IndexError(row)
            msg = f"row {row} is not resolved yet"
            raise RowUnavailable(msg)
        with self._lock:
            self._ranges[row] = found
            while len(self._ranges) > _RANGE_CACHE_ROWS:
                self._ranges.popitem(last=False)
        return found

    def row_class(self, row: int) -> RowClass:
        """Classify the row by its content byte length: short, medium or long."""
        found = self._range(row)
        size = found.content_end - found.start
        if size <= self._config.word_wrap_limit:
            return "short"
        return "medium" if size <= self._config.long_row_threshold else "long"

    def is_long(self, row: int) -> bool:
        return self.row_class(row) == "long"

    def long_index(self, row: int) -> LongLineIndex:
        """Return the long index of `row`, creating it (with autostart) on first use; the least recently used one is cancelled."""
        row = self._norm(row)
        with self._lock:
            index = self._long.get(row)
            if index is not None:
                self._long.move_to_end(row)
                return index
        found = self._range(row)
        with self._lock:
            index = self._long.get(row)
            if index is not None:
                return index
            index = LongLineIndex(
                self._source,
                found.start,
                found.content_end,
                tab_width=self._config.tab_width,
                checkpoint_chars=index_step(self._config),
                yield_seconds=self._config.yield_seconds,
                autostart=False,
            )
            self._long[row] = index
            subscribers = list(self._subscribers)
            while len(self._long) > max(1, self._config.max_long_indexes):
                _, old = self._long.popitem(last=False)
                old.cancel()
                self._retired.append(old)
            overflow = [self._retired.pop(0) for _ in range(max(0, len(self._retired) - _MAX_RETIRED))]
        for callback in subscribers:
            index.subscribe(callback)
        index.start()
        for old in overflow:
            old.join()
        return index

    def anchor_index(self, row: int) -> LongRowAnchorIndex:
        """Return the `AnchorIndex` adapter of a long row (starting its scan when needed); never waits."""
        return LongRowAnchorIndex(self.long_index(row))

    def _text(self, row: int) -> str:
        """Decode a short or medium row (LRU cached); a long row raises `WholeLineAccess`."""
        row = self._norm(row)
        with self._lock:
            cached = self._texts.get(row)
            if cached is not None:
                self._texts.move_to_end(row)
                return cached
        found = self._range(row)
        if found.content_end - found.start > self._config.long_row_threshold:
            self.call_log.refuse("get_line", row)
            msg = f"whole-line access to long row {row}"
            raise WholeLineAccess(msg)
        text = self._source.read(found.start, found.content_end - found.start).decode("utf-8", SURROGATE_ESCAPE)
        self._seen_width = max(self._seen_width, advance_disp(text, 0, self._config.tab_width))
        with self._lock:
            self._texts[row] = text
            while len(self._texts) > max(1, self._config.text_cache_rows):
                self._texts.popitem(last=False)
        return text

    # -- DocumentBase -------------------------------------------------------------------------
    @property
    def line_count(self) -> int:
        """A lower bound while the scan runs (`snapshot().count`)."""
        return self._line_index.snapshot().count

    def get_line(self, index: int) -> str:
        text = self._text(index)
        self.call_log.record("get_line", index, len(text))
        return text

    @overload
    def __getitem__(self, line_index: int) -> str: ...

    @overload
    def __getitem__(self, line_index: slice) -> list[str]: ...

    def __getitem__(self, line_index: int | slice) -> str | list[str]:
        if isinstance(line_index, slice):
            rows = range(*line_index.indices(self.line_count))
            if len(rows) > MAX_SLICE_ROWS:
                self.call_log.refuse("__getitem__", None)
                msg = f"slice of {len(rows)} rows exceeds {MAX_SLICE_ROWS}"
                raise WholeLineAccess(msg)
            return [self.get_line(row) for row in rows]
        return self.get_line(line_index)

    @property
    def text(self) -> str:
        self.call_log.refuse("text", None)
        msg = "whole-document text of a lazy document; use read_all(limit)"
        raise WholeLineAccess(msg)

    @property
    def lines(self) -> list[str]:
        self.call_log.refuse("lines", None)
        msg = "whole-document lines of a lazy document"
        raise WholeLineAccess(msg)

    def read_all(self, limit: int) -> str:
        """Return the whole file decoded, only when the source is at most `limit` bytes; otherwise raise `WholeLineAccess`."""
        length = self._source.length()
        if length > limit:
            self.call_log.refuse("read_all", None)
            msg = f"source of {length} bytes exceeds the limit of {limit}"
            raise WholeLineAccess(msg)
        return self._source.read(0, length).decode("utf-8", SURROGATE_ESCAPE)

    @property
    def newline(self) -> Newline:
        """The terminator of the first row (LF until it is known)."""
        if self._newline is not None:
            return self._newline
        try:
            found = self._range(0)
        except (IndexError, RowUnavailable):
            return "\n"
        terminator = self._source.read(found.content_end, found.end - found.content_end)
        self._newline = "\r\n" if terminator == b"\r\n" else "\r" if terminator == b"\r" else "\n"
        return self._newline

    @property
    def start(self) -> Location:
        """The location of the document start `(0, 0)`; the scan is started with `start_scan()` because this name is taken."""
        return (0, 0)

    @property
    def end(self) -> Location:
        """Location after the last known row; the column of an unfinished long row is its estimate."""
        row = self.line_count - 1
        try:
            length = self.line_length(row)
            if length is None:
                length = self.long_index(row).estimate_length()
        except (IndexError, RowUnavailable):
            length = 0
        return (row, length)

    def get_size(self, indent_width: int) -> Size:
        """Width from the rows seen so far and the estimates of the long rows in use, height `line_count`; never reads."""
        with self._lock:
            indexes = list(self._long.values())
        width = max([self._seen_width, *(index.estimate_display_width() for index in indexes)])
        return Size(width, self.line_count)

    def note_x(self, x: int) -> None:
        """Grow the reported width to at least `x` cells (the widget calls this for what it rendered)."""
        self._seen_width = max(self._seen_width, x)

    def replace_range(self, start: Location, end: Location, text: str) -> EditResult:
        msg = "read-only until ACT4"
        raise NotImplementedError(msg)

    def query_syntax_tree(
        self,
        query: Query,
        start_point: tuple[int, int] | None = None,
        end_point: tuple[int, int] | None = None,
    ) -> dict[str, list[Node]]:
        return {}

    def prepare_query(self, query: str) -> Query | None:
        return None

    def get_text_range(self, start: Location, end: Location) -> str:
        """Text between two locations; refuses a range over more than 128 rows or a long-row part over 8192 characters."""
        if start == end:
            return ""
        (top_row, top_col), (bottom_row, bottom_col) = sorted((start, end))
        if bottom_row - top_row > MAX_SLICE_ROWS:
            self.call_log.refuse("get_text_range", top_row)
            msg = f"range of {bottom_row - top_row + 1} rows exceeds {MAX_SLICE_ROWS}"
            raise WholeLineAccess(msg)
        if top_row == bottom_row:
            return self._part(top_row, top_col, bottom_col)
        parts = [self._part(top_row, top_col, None)]
        parts.extend(self._part(row, 0, None) for row in range(top_row + 1, bottom_row))
        parts.append(self._part(bottom_row, 0, bottom_col))
        return self.newline.join(parts)

    def _part(self, row: int, a: int, b: int | None) -> str:
        """Characters [a, b) of a row (`b=None` for the rest); a long row must be asked for at most 8192 characters."""
        if row >= self.line_count:
            return ""
        if not self.is_long(row):
            return self._text(row)[a:b]
        if b is None or b - a > MAX_WINDOW_CHARS:
            self.call_log.refuse("get_text_range", row)
            msg = f"range over the whole of long row {row}"
            raise WholeLineAccess(msg)
        return self.column_slice(row, a, b)

    # -- capabilities -------------------------------------------------------------------------
    def row_byte_length(self, row: int) -> int:
        found = self._range(row)
        return found.content_end - found.start

    def line_length(self, row: int) -> int | None:
        if self.is_long(row):
            total = self.long_index(row).total_chars
            self.call_log.record("line_length", row, 0)
            return total
        return len(self._text(row))

    def column_slice(self, row: int, start: int, stop: int) -> str:
        start = max(0, start)
        if not self.is_long(row):
            return self._text(row)[start:stop]
        index = self.long_index(row)
        stop = min(stop, start + MAX_WINDOW_CHARS)
        pieces: list[str] = []
        pos = start
        while pos < stop:
            piece = index.try_get_slice(pos, stop)
            if not piece:
                break
            pieces.append(piece)
            pos += len(piece)
        result = "".join(pieces)
        self.call_log.record("column_slice", row, len(result))
        return result

    def has_char_at(self, row: int, column: int) -> bool:
        """Whether the row has a character at `column`; `False` when that part of a long row is not scanned yet."""
        if not self.is_long(row):
            return 0 <= column < len(self._text(row))
        piece = self.long_index(row).try_get_slice(column, column + 1)
        self.call_log.record("has_char_at", row, len(piece or ""))
        return bool(piece)

    def display_column(self, row: int, column: int, tab_width: int = 4) -> int | None:
        """Display column of `column`; long rows use the document's `tab_width` and return `None` beyond the frontier."""
        column = max(0, column)
        if not self.is_long(row):
            return advance_disp(self._text(row)[:column], 0, tab_width)
        index = self.long_index(row)
        self.call_log.record("display_column", row, column % index_step(self._config))
        return index.try_char_to_disp(column)

    def column_at_display(self, row: int, x: int, tab_width: int = 4) -> int | None:
        """Character column covering display column `x`; long rows use the document's `tab_width` and return `None` beyond the frontier."""
        if not self.is_long(row):
            return locate_cover(self._text(row), 0, x, tab_width)[0]
        self.call_log.record("column_at_display", row, index_step(self._config))
        return self.long_index(row).try_disp_to_char(x)

    def byte_offset(self, row: int, column: int) -> int | None:
        """Absolute byte offset of a location, or `None` when it lies beyond the frontier of a long row."""
        column = max(0, column)
        found = self._range(row)
        if not self.is_long(row):
            return found.start + utf8_len(self._text(row)[:column])
        index = self.long_index(row)
        self.call_log.record("byte_offset", row, column % index_step(self._config))
        relative = index.try_char_to_byte(column)
        return None if relative is None else found.start + relative
