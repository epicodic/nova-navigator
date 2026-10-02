"""Editable `DocumentBase` over the core piece table: rows are decoded on demand, long rows only through windows (ACT3 design 4.1 to 4.3, ACT4 design 7).

The document layer is the only one that touches `nova_editor.core`. No call waits for a scan: whatever is not scanned yet
is reported as `None` (or an empty slice) and the caller asks again after a subscriber notification.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple, overload

from textual.geometry import Size

from nova_editor.core import (
    AddStore,
    ByteSource,
    BytesSource,
    ChangeKind,
    Content,
    LineIndex,
    LineSnapshot,
    LongLineIndex,
    OriginalSource,
    PieceSource,
    PieceTable,
    PreadSource,
    RebasePlan,
    Rebaser,
    RowNotIndexed,
    RowRange,
    SourceChanged,
)
from nova_editor.core.foreground import Foreground
from nova_editor.core.line_index import call_subscriber
from nova_editor.core.long_line_index import SPLICE_SYNC_BYTES
from nova_editor.core.long_line_index import Edit as LongEdit
from nova_editor.core.pieces import MAX_GENERATION
from nova_editor.core.rebase import UNDO_COPY_LIMIT
from nova_editor.core.save import PlanPart, SaveResult
from nova_editor.core.text_width import SURROGATE_ESCAPE, advance_disp, locate_cover, utf8_len
from nova_editor.document._document import DocumentBase, EditResult, Location, Newline
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._long_row_anchor import LongRowAnchorIndex

if TYPE_CHECKING:
    from tree_sitter import Node, Query

    from nova_editor.document._syntax_aware_document import SyntaxAwareDocument

MAX_WINDOW_CHARS = 8192
"""The most characters any capability call decodes from a long row."""
MAX_SLICE_ROWS = 128
"""The most rows one `__getitem__` slice or `get_text_range` call touches."""
_RANGE_CACHE_ROWS = 4096
_MAX_RETIRED = 64
_MAX_EVENTS = 10_000
_FIRST_ROW_POLL = 0.002
REPLACED_TEXT_LIMIT = 64 * 1024
"""`EditResult.replaced_text` is filled only when at most this many bytes were removed."""
RELOCATE_LIMIT = 256 * 1024
"""The most bytes the end location of an edit that merged UTF-8 characters re-decodes; beyond it an old long index anchors the count, or the end column is approximate."""
_MERGE_CONTEXT = 3
_NEWLINE = re.compile(r"\r\n|\r|\n")
_CRLF = b"\r\n"

RowClass = Literal["short", "medium", "long"]


class WholeLineAccess(AssertionError):
    """A caller asked for the whole text of a long row (or of the whole document)."""


class RowUnavailable(RuntimeError):
    """The row exists but its byte range is not resolvable yet: the scan has not reached its end, or the read budget was exceeded."""


class EditsLocked(RowUnavailable):
    """The document refuses edits at the moment (`LazyDocument.lock_edits`); the message holds the reason."""


class _Swap(NamedTuple):
    """What `apply_rebase` does after the table was replaced and the lock is released."""

    subscribers: list[Callable[[], None]]
    rebased: list[LongLineIndex]
    retired: list[LongLineIndex]
    reapers: list[threading.Thread]
    release: list[tuple[LineIndex, ByteSource]]


def _release(files: list[tuple[LineIndex, ByteSource]]) -> None:
    """Join the (cancelled) line scan of each file, then close its source; every source is closed even when an earlier one fails."""
    if not files:
        return
    index, source = files[0]
    try:
        index.join()
    finally:
        try:
            source.close()
        finally:
            _release(files[1:])


class _SegmentBytes:
    """`ByteSource` view of a piece source (an add segment or a legacy original) for a save plan: `read(offset, size)` instead of `read(a, b)`."""

    def __init__(self, source: PieceSource) -> None:
        self._source = source

    def length(self) -> int:
        """Return an upper bound; the plan never reads beyond the pieces it names."""
        return 1 << 62

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        """Return the bytes `[offset, offset + size)` of the piece source; `cache` is accepted for the protocol."""
        del cache
        return self._source.read(offset, offset + size)

    def close(self) -> None:
        """Do nothing: the piece source belongs to the table."""


_EMPTY = Content.from_pieces([], 0)


def _decodes_differently(left: bytes, middle: bytes, right: bytes) -> bool:
    """Whether the three byte runs decode to other characters joined than apart (a UTF-8 sequence spans a junction)."""
    parts = [part.decode("utf-8", SURROGATE_ESCAPE) for part in (left, middle, right)]
    return "".join(parts) != (left + middle + right).decode("utf-8", SURROGATE_ESCAPE)


def _span_tail_chars(first: _Located, last: _Located) -> int:
    """Characters after the last break between two locations (`first` not after `last`): the columns tell, so no byte is read."""
    return last.column - first.column if first.row == last.row else last.column


class _Located(NamedTuple):
    """A location resolved to a byte offset: the clamped row and column, the row range and the decoded row (`None` for a long row)."""

    row: int
    column: int
    offset: int
    found: RowRange
    text: str | None


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
        self._edit_locks: list[str] = []
        """The reasons that refuse edits at the moment, oldest first; the latest one is reported."""
        self._saving = False
        self._save_edits: int | None = None
        self._edit_count = 0
        self._close_done = threading.Event()
        self._reapers: list[threading.Thread] = []
        self._rebase_closers: list[threading.Thread] = []
        self._legacy_files: list[tuple[LineIndex, ByteSource]] = []
        """The line index and source behind each legacy generation of the table (generation `g` is element `g - 1`); closed with the document."""
        self._started = False
        self.foreground = Foreground()
        """Touched by the widget on key and mouse events; the scans of this document give way to the UI while it is."""
        self._line_index = LineIndex(
            source,
            stride=self._config.stride,
            long_line_threshold=self._config.index_long_line_threshold,
            yield_seconds=self._config.yield_seconds,
            foreground=self.foreground,
            scan_block=self._config.scan_block,
        )
        self._add_store = AddStore()
        self._table = self._build_table(source, self._line_index, self._add_store)
        self._ranges: OrderedDict[int, RowRange] = OrderedDict()
        self._texts: OrderedDict[int, str] = OrderedDict()
        self._long: OrderedDict[int, LongLineIndex] = OrderedDict()
        self._retired: list[LongLineIndex] = []
        self._subscribers: list[Callable[[], None]] = []
        self._newline: Newline | None = None
        self._syntax: SyntaxAwareDocument | None = None
        self._seen_width = 0
        self.call_log = CallLog()
        if autostart:
            if source.length() <= self._config.sync_scan_limit:
                self._scan_inline()
            else:
                self.start_scan()

    @classmethod
    def from_path(cls, path: Path | str, config: LazyConfig | None = None, *, autostart: bool = True) -> LazyDocument:
        """Open `path` through a `PreadSource` that the document owns and closes."""
        return cls(PreadSource(path), config, autostart=autostart)

    @classmethod
    def from_bytes(cls, data: bytes, config: LazyConfig | None = None) -> LazyDocument:
        """Open `data` held in memory; up to `LazyConfig.sync_scan_limit` bytes the counts are exact immediately."""
        return cls(BytesSource(data), config)

    @classmethod
    def from_text(cls, text: str, config: LazyConfig | None = None) -> LazyDocument:
        """Open `text` encoded as UTF-8 (lone surrogates from `SURROGATE_ESCAPE` round-trip to their original bytes)."""
        return cls.from_bytes(text.encode("utf-8", SURROGATE_ESCAPE), config)

    @staticmethod
    def _build_table(source: ByteSource, line_index: LineIndex, add_store: AddStore) -> PieceTable:
        """Create the piece table over `source` (the lock audit replaces this to wrap the table it builds)."""
        return PieceTable(source, line_index, add_store)

    # -- lifecycle ----------------------------------------------------------------------------
    @property
    def tab_width(self) -> int:
        """The tab width the long-row indexes use (`LazyConfig.tab_width`); the widget sets it from its `indent_width` at `open`."""
        return self._config.tab_width

    def start_scan(self) -> None:
        """Start the background line scan; a second call, or a call after `close`, does nothing."""
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
        self._line_index.start()

    def _scan_inline(self) -> None:
        """Run the whole line scan on the calling thread (construction of small documents); the table sees the complete index at once."""
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
        self._line_index.scan_now()

    def wait_first_row(self, timeout: float) -> bool:
        """Wait until the line scan has resolved row 0 (or knows there is none); return whether it did within `timeout` seconds (a first block takes milliseconds)."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._range(0)
            except IndexError:
                return True
            except RowUnavailable:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(_FIRST_ROW_POLL)
            else:
                return True

    def wait_indexed(self, timeout: float) -> bool:
        """Wait for the line scan to complete (tests and workers only); return whether it completed."""
        return self._line_index.join(timeout)

    def close(self) -> None:
        """Cancel every scan on the calling thread, then join them and close the source on a daemon "lazy-closer" thread; idempotent.

        The caller (the UI thread) never waits: a join, or `PreadSource.close` waiting for an in-flight read, runs on the closer.
        The order is cancel, join, close the source. `wait_closed` waits for the closer (tests, shutdown code).
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            indexes = [*self._long.values(), *self._retired]
            self._long.clear()
            self._retired.clear()
            reapers = [*self._reapers, *self._rebase_closers]
            legacy = list(self._legacy_files)
            line_index, source = self._line_index, self._source
        line_index.cancel()
        for index in indexes:
            index.cancel()
        for old_index, _ in legacy:
            old_index.cancel()
        files = [*legacy, (line_index, source)]
        threading.Thread(target=self._finish_close, args=(indexes, reapers, files), name="lazy-closer", daemon=True).start()

    def _finish_close(self, indexes: list[LongLineIndex], reapers: list[threading.Thread], files: list[tuple[LineIndex, ByteSource]]) -> None:
        """Closer thread: join the cancelled scans, then close the sources (the current one and those of the legacy generations)."""
        try:
            for index in indexes:
                index.join()
            for reaper in reapers:
                reaper.join()
        finally:
            try:
                _release(files)
            finally:
                self._close_done.set()

    def join_on_close(self, thread: threading.Thread) -> None:
        """Make `close` wait for `thread` (a started thread that reads or writes through this document) before it closes the source."""
        with self._lock:
            self._rebase_closers = [closer for closer in self._rebase_closers if closer.is_alive()]
            self._rebase_closers.append(thread)

    def wait_closed(self, timeout: float) -> bool:
        """Wait until `close` has joined every scan and closed the source; return whether that happened within `timeout` seconds."""
        return self._close_done.wait(timeout)

    def lock_edits(self, reason: str) -> None:
        """Refuse `replace_range`, `splice` and `splice_bytes` with `EditsLocked(reason)` until `unlock_edits`.

        Reasons are kept apart: locking with a second reason does not replace the first, and the latest one is the one reported.
        """
        with self._lock:
            if reason in self._edit_locks:
                self._edit_locks.remove(reason)
            self._edit_locks.append(reason)

    def unlock_edits(self, reason: str | None = None) -> None:
        """Lift the lock `reason` (one that is not held changes nothing); without a reason lift every lock. Edits are accepted again when none is left."""
        with self._lock:
            if reason is None:
                self._edit_locks.clear()
            elif reason in self._edit_locks:
                self._edit_locks.remove(reason)

    def check_source(self) -> ChangeKind:
        """Check the descriptor of a large original without reading content: `UNCHANGED`, or why a read would fail. A source in memory is `UNCHANGED`."""
        with self._lock:
            source = self._source
        if not isinstance(source, PreadSource):
            return ChangeKind.UNCHANGED
        try:
            source.check()
        except SourceChanged as error:
            return error.kind
        return ChangeKind.UNCHANGED

    def begin_save(self) -> None:
        """Register a running save: `require_not_saving` refuses until `end_save`."""
        with self._lock:
            self._saving = True
            self._save_edits = self._edit_count

    def end_save(self) -> None:
        """Clear the registration of a save."""
        with self._lock:
            self._saving = False
            self._save_edits = None

    @property
    def saving(self) -> bool:
        """Whether a save is registered (`begin_save` without `end_save`)."""
        with self._lock:
            return self._saving

    def require_not_saving(self, operation: str) -> None:
        """Guard for `load_text`, `reload` and `open` of the widget.

        Raises:
            RuntimeError: A save is registered; `operation` names the refused call.
        """
        if self.saving:
            msg = f"{operation} is not allowed while a save runs"
            raise RuntimeError(msg)

    def plan(self, offset: int, limit: int, unverified: bool) -> list[PlanPart]:
        """Return the parts that make up the document bytes `[offset, offset + limit)` (clamped to the document), in order, for the save job.

        The lock is held for this call only, never across I/O: no byte is read here.
        Parts of the original (`src` 0) carry the document's source, or with `unverified` a reader of its descriptor that skips the size and mtime
        check; the other parts carry a view of their add segment or legacy original with `read(offset, size)` semantics.

        Raises:
            RowUnavailable: The document is closed.
        """
        with self._lock:
            self._require_open()
            end = min(offset + limit, self._table.length)
            start = min(offset, end)
            runs = self._table.layout_range(start, end)
            original: ByteSource = self._source
            if unverified and isinstance(original, PreadSource):
                original = original.unverified_reader()
            return [PlanPart(src, original if src == 0 else _SegmentBytes(self._table._source_of(src)), a, b) for src, a, b in runs]

    def prepare_rebase(self, result: SaveResult, contents: Sequence[Content]) -> RebasePlan:
        """Translate `contents` (every piece reference the undo and redo stacks and the clipboard hold) onto the saved file (design 7.3 and 8).

        Runs on the save thread after the commit and does the heavy work; it touches no table state (the lock is taken only to read which source,
        line index and add store are current), so edits must stay locked until `apply_rebase`.
        The plan carries the saved file's source and line index: the caller hands it to `apply_rebase`, which then owns them.

        Raises:
            ValueError: The save built no line index.
        """
        if result.line_index is None:
            msg = "a rebase needs the line index of the saved file"
            raise ValueError(msg)
        with self._lock:
            source, line_index, store = self._source, self._line_index, self._add_store
            generations = len(self._legacy_files)
        original = OriginalSource(line_index, source)
        old_sources: list[PieceSource] = [original, *(store.segment(segment) for segment in range(1, store.segment_count + 1))]
        rebaser = Rebaser(
            result.layout,
            old_sources,
            OriginalSource(result.line_index, result.source),
            AddStore(),
            limit=UNDO_COPY_LIMIT,
            max_generation=MAX_GENERATION,
            legacy=(original, store),
            generations=generations,
        )
        plan = rebaser.translate(contents)
        plan.source = result.source
        plan.line_index = result.line_index
        return plan

    def apply_rebase(self, plan: RebasePlan) -> None:
        """Switch the document onto the saved file (UI thread; bounded work, under the document lock; design 7.3).

        In order: build the new table over the file with a fresh add store; carry the legacy generations and register the one the plan keeps;
        replace source, line index and table; clear the row-range and text caches; rebase the cached long indexes (one that cannot be rebased is
        dropped and rebuilt on demand); retire the old indexes, scan and add store; close the old source on a closer thread once the scans that read
        it have returned (it stays open when a legacy generation keeps it).
        On a closed document the plan's source is closed and nothing else happens.
        The caller then writes the translated contents back into its records (`Edit.rewrite`), or clears them when `plan.clear_history` is set.

        Raises:
            ValueError: The plan has no source, or the document changed since the save started (the caller then closes the plan's source).
        """
        source, line_index = plan.source, plan.line_index
        if source is None or line_index is None:
            msg = "the plan carries no saved file"
            raise ValueError(msg)
        with self._lock:
            swap = None if self._closed else self._install(plan, source, line_index)
        if swap is None:
            source.close()
            return
        for old_index, _ in swap.release:
            old_index.cancel()
            old_index.clear_subscribers()
        for retired in swap.retired:
            retired.clear_subscribers()
        for callback in swap.subscribers:
            line_index.subscribe(callback)
        for replacement in swap.rebased:
            for callback in swap.subscribers:
                replacement.subscribe(callback)
            replacement.start()
        closer = threading.Thread(target=self._finish_rebase, args=(swap.retired, swap.reapers, swap.release), name="lazy-rebase-closer", daemon=True)
        with self._lock:
            self._rebase_closers = [thread for thread in self._rebase_closers if thread.is_alive()]
            self._rebase_closers.append(closer)
        closer.start()

    def _install(self, plan: RebasePlan, source: ByteSource, line_index: LineIndex) -> _Swap:
        """Replace source, line index and table by the saved file (the caller holds the lock); return what is left to do outside it.

        Raises:
            ValueError: The document changed since the save started; nothing is changed.
        """
        if (self._save_edits is not None and self._save_edits != self._edit_count) or source.length() != self._table.length:
            msg = "the document changed since the save started"
            raise ValueError(msg)
        old_source, old_index, old_table = self._source, self._line_index, self._table
        table = self._build_table(source, line_index, plan.new_add_store)
        kept: list[tuple[LineIndex, ByteSource]] = []
        release: list[tuple[LineIndex, ByteSource]] = []
        if plan.clear_history:
            release.extend(self._legacy_files)  # nothing refers to a legacy generation after the history is cleared
        else:
            kept.extend(self._legacy_files)
            for original, store in old_table.legacy:
                table.register_legacy(original, store)
        if plan.retained is not None:
            table.register_legacy(*plan.retained)
            kept.append((old_index, old_source))  # the retained generation reads the old file, so its scan goes on and the file stays open
        else:
            release.append((old_index, old_source))
        self._legacy_files = kept
        self._source, self._line_index, self._add_store, self._table = source, line_index, plan.new_add_store, table
        self._ranges.clear()
        self._texts.clear()
        old_longs = self._long
        self._long = OrderedDict()
        rebased = self._rebase_long(table, old_longs)
        retired = [*self._retired, *old_longs.values()]
        self._retired = []
        return _Swap(list(self._subscribers), rebased, retired, list(self._reapers), release)

    def _rebase_long(self, table: PieceTable, old_longs: OrderedDict[int, LongLineIndex]) -> list[LongLineIndex]:
        """Cancel the old long indexes and move them onto the new table (the caller holds the lock and has to start the results).

        An index whose row cannot be resolved or rebased is dropped; it is built again when the row is asked for.
        """
        rebased: list[LongLineIndex] = []
        for row, old in old_longs.items():
            old.cancel()
            found = table.row_range(row)
            if found is None:
                continue
            try:
                replacement = LongLineIndex.rebased(old, table.row_source(found.start, found.content_end), autostart=False)
            except (ValueError, SourceChanged):
                continue
            if replacement is not None:
                self._long[row] = replacement
                rebased.append(replacement)
        return rebased

    @staticmethod
    def _finish_rebase(indexes: list[LongLineIndex], reapers: list[threading.Thread], release: list[tuple[LineIndex, ByteSource]]) -> None:
        """Closer thread of a rebase: join the cancelled scans of the old table, then close the old files that no generation keeps."""
        try:
            for index in indexes:
                index.join()
            for reaper in reapers:
                reaper.join()
        finally:
            _release(release)

    def wait_rebased(self, timeout: float) -> bool:
        """Wait until the closers of earlier rebases have closed the old files (tests and shutdown code); return whether they did within `timeout` seconds."""
        with self._lock:
            closers = list(self._rebase_closers)
        deadline = time.monotonic() + timeout
        for closer in closers:
            closer.join(max(deadline - time.monotonic(), 0.0))
        return not any(closer.is_alive() for closer in closers)

    def _require_editable(self) -> None:
        """Raise `EditsLocked` while edits are locked; the caller holds the lock."""
        if self._edit_locks:
            raise EditsLocked(self._edit_locks[-1])

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
        snap = self.snapshot()
        if not snap.complete and snap.error is None:
            return True
        return any(index.running for index in indexes)

    def snapshot(self) -> LineSnapshot:
        """Return the consistent state of the line scan (row count and scanned bytes in document coordinates)."""
        with self._lock:
            return self._table.snapshot()

    @property
    def length(self) -> int:
        """Length of the document in bytes."""
        with self._lock:
            return self._table.length

    def row_at_offset(self, offset: int) -> tuple[int, RowRange] | None:
        """Return `(row, range)` of the row that holds byte `offset` (see `LineIndex.row_at_offset`), or `None` when it is not known yet."""
        with self._lock:
            return self._table.row_at_offset(offset)

    def read_bytes(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        """Return up to `size` document bytes from `offset`; an unedited document reads the original source directly."""
        with self._lock:
            if self._table.is_identity:
                return self._source.read(offset, size, cache=cache)
            return self._table.read(offset, size)

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
        with self._lock:
            found = self._table.row_range(row)
        if found is None:
            snap = self.snapshot()
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
        """Return the long index of `row`, creating it (with autostart) on first use; the least recently used one is cancelled.

        A new index scans a `RowSource` of the pieces of the row (a single piece for an unedited row), never the table.
        An edit inside the row replaces the index by a spliced one, edits above only change its key (design 6.3).

        Raises:
            RowUnavailable: The document is closed (no scan is ever started on a closed source).
        """
        row = self._norm(row)
        with self._lock:
            self._require_open()
        found = self._range(row)
        with self._lock:
            self._require_open()
            index = self._long.get(row)
            if index is not None and index.end_offset - index.start_offset == found.content_end - found.start:
                self._long.move_to_end(row)
                return index
            if index is not None:  # stale: the key and the row disagree (never expected, kept as a guard)
                del self._long[row]
                index.cancel()
                self._retired.append(index)
            rows = self._table.row_source(found.start, found.content_end)
            index = LongLineIndex(
                rows,
                0,
                rows.length(),
                tab_width=self._config.tab_width,
                checkpoint_chars=index_step(self._config),
                yield_seconds=self._config.yield_seconds,
                foreground=self.foreground,
                scan_block=self._config.scan_block,
                autostart=False,
            )
            self._long[row] = index
            subscribers = list(self._subscribers)
            while len(self._long) > max(1, self._config.max_long_indexes):
                _, old = self._long.popitem(last=False)
                old.cancel()
                self._retired.append(old)
            overflow = self._take_overflow()
        for callback in subscribers:
            index.subscribe(callback)
        index.start()
        self._reap(overflow)
        return index

    def _take_overflow(self) -> list[LongLineIndex]:
        """Drop the retired indexes whose scan thread has returned, then remove and return the oldest ones beyond the limit; the caller holds the lock.

        A retired index holds its checkpoint arrays and a snapshot of the pieces of its row, so one that nothing has to join must not stay alive.
        A scan still in a read stays listed until it returns, so `close` and the reaper can join it.
        """
        self._retired = [index for index in self._retired if not index.quiescent()]
        return [self._retired.pop(0) for _ in range(max(0, len(self._retired) - _MAX_RETIRED))]

    def _reap(self, overflow: list[LongLineIndex]) -> None:
        """Join cancelled scans that fell out of the retired list on a reaper thread."""
        if not overflow:
            return
        reaper = threading.Thread(target=self._join_all, args=(overflow,), name="lazy-reaper", daemon=True)
        with self._lock:
            self._reapers = [t for t in self._reapers if t.is_alive()]
            self._reapers.append(reaper)
        reaper.start()

    @staticmethod
    def _join_all(indexes: list[LongLineIndex]) -> None:
        """Reaper thread: join scans that were cancelled when they fell out of the retired list."""
        for index in indexes:
            index.join()

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
        text = self.read_bytes(found.start, found.content_end - found.start).decode("utf-8", SURROGATE_ESCAPE)
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
        return self.snapshot().count

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
        with self._lock:
            length = self._table.length
            data = self._table.read(0, length) if length <= limit else b""
        if length > limit:
            self.call_log.refuse("read_all", None)
            msg = f"source of {length} bytes exceeds the limit of {limit}"
            raise WholeLineAccess(msg)
        return data.decode("utf-8", SURROGATE_ESCAPE)

    @property
    def newline(self) -> Newline:
        """The terminator of the first row (LF until it is known)."""
        if self._newline is not None:
            return self._newline
        try:
            found = self._range(0)
        except (IndexError, RowUnavailable):
            return "\n"
        terminator = self.read_bytes(found.content_end, found.end - found.content_end)
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

    # -- editing ------------------------------------------------------------------------------
    def replace_range(self, start: Location, end: Location, text: str) -> EditResult:
        """Replace the text between two locations (sorted if needed); newlines in `text` become one terminator (ACT4 design 7.3).

        The terminator is the one of the row that contains the start (the document newline when that row has none), changed
        when it would otherwise merge with a neighbour: after a CR it must not start with LF, before an LF it must not end with CR.
        `EditResult.replaced_text` is the removed text when it is at most 64 KiB, otherwise `""`; `removed` always holds the removed bytes.

        Raises:
            RowUnavailable: The document is closed, a row or column is not resolved yet, or the edit would merge UTF-8 characters in a long row.
            IndexError: The start row is negative.
        """
        top, bottom = sorted((start, end))
        with self._lock:
            self._require_open()
            self._require_editable()
            first = self._locate(top)
            last = self._locate(bottom)
            if _NEWLINE.search(text):
                terminator = self._terminator(first, last)
                text = _NEWLINE.sub(lambda _: terminator, text)
            data = text.encode("utf-8", SURROGATE_ESCAPE)
            content = self._table.add(data) if data else _EMPTY
            result = self._commit(first, last, content)
            mirror = self._syntax
        if mirror is not None:
            mirror.replace_range(top, bottom, text)
        self._notify_subscribers()
        return result

    def splice(self, start: Location, end: Location, content: Content) -> EditResult:
        """Replace the text between two locations by `content`, byte for byte (undo, redo and paste of internal content).

        Nothing is normalised.
        The end location comes from `Content.breaks` and `Content.tail_chars`, or from a re-decode of the end row when characters merge.

        Raises:
            RowUnavailable: As for `replace_range`.
            IndexError: The start row is negative.
        """
        top, bottom = sorted((start, end))
        with self._lock:
            self._require_open()
            self._require_editable()
            first = self._locate(top)
            last = self._locate(bottom)
        return self._splice_located(first, last, content)

    def selection_content(self, start: Location, end: Location) -> Content:
        """The bytes between two locations (sorted if needed) as piece references; no data is read (clipboard copy and cut).

        Raises:
            RowUnavailable: The document is closed or a row or column is not resolved yet.
            IndexError: A row is negative.
        """
        top, bottom = sorted((start, end))
        with self._lock:
            self._require_open()
            first = self._locate(top)
            last = self._locate(bottom)
            return self._table.content(first.offset, last.offset, _span_tail_chars(first, last))

    def content_text(self, content: Content) -> str:
        """The decoded text of `content` (invalid bytes as U+DC80 to U+DCFF); it reads every byte, so ask only for small contents."""
        with self._lock:
            return self._table.content_bytes(content, 0, content.length).decode("utf-8", SURROGATE_ESCAPE)

    def splice_bytes(self, start_byte: int, end_byte: int, content: Content) -> EditResult:
        """Replace the bytes `[start_byte, end_byte)` by `content` (undo and redo: the offsets are exact, the locations are derived from them).

        Raises:
            RowUnavailable: The document is closed or a position is not resolved yet.
            ValueError: The offsets are not ordered or lie beyond the document.
        """
        if not 0 <= start_byte <= end_byte:
            msg = f"invalid byte range {start_byte}..{end_byte}"
            raise ValueError(msg)
        with self._lock:
            self._require_open()
            self._require_editable()
            if end_byte > self._table.length:
                msg = f"byte {end_byte} is beyond the document"
                raise ValueError(msg)
            # A boundary inside a CRLF (undo of a deletion that joined a CR and an LF) cannot be spliced: take the halves along.
            if start_byte > 0 and self._table.read(start_byte - 1, 2) == _CRLF:
                start_byte -= 1
                head = self._table.byte_content(start_byte)
                content = Content.from_pieces([*head.pieces, *content.pieces], content.tail_chars)
            if end_byte > 0 and self._table.read(end_byte - 1, 2) == _CRLF:
                tail = self._table.byte_content(end_byte)
                content = Content.from_pieces([*content.pieces, *tail.pieces], 0)
                end_byte += 1
            first = self._locate_offset(start_byte)
            last = first if end_byte == start_byte else self._locate_offset(end_byte)
        return self._splice_located(first, last, content)

    def _splice_located(self, first: _Located, last: _Located, content: Content) -> EditResult:
        with self._lock:
            self._require_open()
            self._require_editable()
            result = self._commit(first, last, content)
            mirror = self._syntax
            inserted = self._table.content_bytes(content, 0, content.length) if mirror is not None else b""
        if mirror is not None:
            mirror.replace_range((first.row, first.column), (last.row, last.column), inserted.decode("utf-8", SURROGATE_ESCAPE))
        self._notify_subscribers()
        return result

    def _locate_offset(self, offset: int) -> _Located:
        """Resolve a byte offset (on a character boundary) to row and column; the caller holds the lock."""
        found = self._table.row_at_offset(offset)
        if found is None:
            msg = f"byte {offset} is not resolved yet"
            raise RowUnavailable(msg)
        row, span = found
        relative = offset - span.start
        if offset > span.content_end:
            msg = f"byte {offset} is inside a line terminator"
            raise ValueError(msg)
        if not self.is_long(row):
            text = self._text(row)
            column = len(self._table.read(span.start, relative).decode("utf-8", SURROGATE_ESCAPE))
            return _Located(row, column, span.start + relative, span, text)
        column = self.long_index(row).byte_to_char(relative)
        if column is None:
            msg = f"byte {offset} of long row {row} is not resolved yet"
            raise RowUnavailable(msg)
        return _Located(row, column, span.start + relative, span, None)

    def _require_open(self) -> None:
        if self._closed:
            msg = "document is closed"
            raise RowUnavailable(msg)

    def _notify_subscribers(self) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            call_subscriber(callback)

    def _locate(self, location: Location) -> _Located:
        """Resolve a location to a byte offset; a column beyond the row is clamped, a row beyond the document means its end."""
        row, column = location
        if row < 0:
            raise IndexError(row)
        column = max(column, 0)
        count = self.line_count
        if row >= count:
            if not self.snapshot().complete:
                msg = f"row {row} is not resolved yet"
                raise RowUnavailable(msg)
            row, column = count - 1, 1 << 62
        found = self._range(row)
        if not self.is_long(row):
            text = self._text(row)
            column = min(column, len(text))
            return _Located(row, column, found.start + utf8_len(text[:column]), found, text)
        index = self.long_index(row)
        total = index.total_chars
        if total is not None:
            column = min(column, total)
        relative = index.try_char_to_byte(column)
        if relative is None:
            msg = f"column {column} of long row {row} is not resolved yet"
            raise RowUnavailable(msg)
        return _Located(row, column, found.start + relative, found, None)

    def _terminator(self, first: _Located, last: _Located) -> str:
        """Return the terminator `T` for newlines inserted over `[first, last)` (ACT4 design 7.3)."""
        row_terminator = self._table.read(first.found.content_end, first.found.end - first.found.content_end)
        base = row_terminator.decode("ascii") if row_terminator else self.newline
        left = self._table.read(first.offset - 1, 1) if first.offset > 0 else b""
        right = self._table.read(last.offset, 1)
        for candidate in (base, "\n", "\r\n", "\r"):
            if left == b"\r" and candidate.startswith("\n"):
                continue
            if right == b"\n" and candidate.endswith("\r"):
                continue
            return candidate
        return "\r\n"

    def _merges(self, left: bytes, content: Content, right: bytes) -> bool:
        """Whether decoding the bytes around the junctions of an edit differs from decoding each side alone (at most 3 bytes per side)."""
        if content.length <= 2 * _MERGE_CONTEXT:
            return _decodes_differently(left, self._table.content_bytes(content, 0, content.length), right)
        head = self._table.content_bytes(content, 0, _MERGE_CONTEXT)
        tail = self._table.content_bytes(content, content.length - _MERGE_CONTEXT, content.length)
        return _decodes_differently(left, head, b"") or _decodes_differently(b"", tail, right)

    def _commit(self, first: _Located, last: _Located, content: Content) -> EditResult:
        """Splice the table and bring caches, long indexes and the newline up to date; the caller holds the lock."""
        begin, finish = first.offset, last.offset
        left = self._table.read(max(begin - _MERGE_CONTEXT, 0), min(begin, _MERGE_CONTEXT))
        right = self._table.read(finish, _MERGE_CONTEXT)
        merging = self._merges(left, content, right)
        joins = content.length == 0 and left[-1:] == b"\r" and right[:1] == b"\n"  # a CR and an LF became one CRLF: the row structure changed
        old_index = self._long.get(first.row) if first.text is None else None
        plan = None
        if old_index is not None and not merging and not joins and first.row == last.row and content.breaks == 0:
            plan = self._plan_splice(old_index, first, last, content)
        try:
            removed = self._table.splice(begin, finish, content, _span_tail_chars(first, last))
        except RowNotIndexed as error:
            msg = f"offset {finish} is not scanned yet"
            raise RowUnavailable(msg) from error
        self._edit_count += 1
        head_joined, tail_joined = self._crlf_formed(content, left, right)
        row_delta = content.breaks - (last.row - first.row) - head_joined - tail_joined
        replacement = None
        if plan is not None:
            size = first.found.content_end - first.found.start - (finish - begin) + content.length
            replacement = self._splice_index(old_index, first.found.start, size, plan)
        if merging:
            end_location = self._relocate(begin + content.length, first, content, old_index)
        elif content.breaks:
            end_location = (first.row + content.breaks - head_joined, content.tail_chars)
        else:
            end_location = (first.row, first.column + content.tail_chars)
        patch = None
        if not merging and not joins and first.row == last.row and content.breaks == 0 and first.text is not None:
            size = first.found.content_end - first.found.start - (finish - begin) + content.length
            if size <= self._config.long_row_threshold:
                inserted = self._table.content_bytes(content, 0, content.length).decode("utf-8", SURROGATE_ESCAPE)
                patch = first.text[: first.column] + inserted + first.text[last.column :]
        self._after_edit(first.row, last.row, row_delta, replacement, patch)
        replaced = ""
        if removed.length <= REPLACED_TEXT_LIMIT:
            replaced = self._table.content_bytes(removed, 0, removed.length).decode("utf-8", SURROGATE_ESCAPE)
        return EditResult(end_location, replaced, removed, begin, content)

    def _crlf_formed(self, content: Content, left: bytes, right: bytes) -> tuple[int, int]:
        """Whether an edit joined a CR and an LF into one CRLF terminator (each one removes a row) at its left and at its right junction."""
        if content.length == 0:
            return (int(left[-1:] == b"\r" and right[:1] == b"\n"), 0)
        head = self._table.content_bytes(content, 0, 1)
        tail = self._table.content_bytes(content, content.length - 1, content.length)
        return (int(left[-1:] == b"\r" and head == b"\n"), int(tail == b"\r" and right[:1] == b"\n"))

    def _plan_splice(self, old: LongLineIndex, first: _Located, last: _Located, content: Content) -> LongEdit | None:
        """Measure an edit inside one long row for `LongLineIndex.spliced`, before the table changes; `None` when the index is rebuilt instead.

        The measures come from the queries that located the edit.
        A row that is no longer long afterwards, or an insertion over `SPLICE_SYNC_BYTES` (its width would have to be scanned on this thread), is rebuilt.

        Raises:
            RowUnavailable: The old index cannot answer for the edited columns (they are not scanned yet).
        """
        size = first.found.content_end - first.found.start - (last.offset - first.offset) + content.length
        if size <= self._config.long_row_threshold or content.length > SPLICE_SYNC_BYTES:
            return None
        disp_first = old.try_char_to_disp(first.column)
        disp_last = old.try_char_to_disp(last.column)
        if disp_first is None or disp_last is None:
            msg = f"columns of long row {first.row} are not resolved yet"
            raise RowUnavailable(msg)
        text = self._table.content_bytes(content, 0, content.length).decode("utf-8", SURROGATE_ESCAPE)
        added_disp = advance_disp(text, disp_first, self._config.tab_width) - disp_first
        return LongEdit(first.offset - first.found.start, last.offset - first.offset, last.column - first.column, disp_last - disp_first, content.length, len(text), added_disp)

    def _splice_index(self, old: LongLineIndex | None, row_start: int, size: int, edit: LongEdit) -> LongLineIndex | None:
        """Build the index of the edited row from `old` (design 6.2); `None` when that fails, and the row is indexed again from scratch when asked for."""
        if old is None:
            return None
        rows = self._table.row_source(row_start, row_start + size)
        try:
            return LongLineIndex.spliced(old, rows, edit, autostart=False)
        except (ValueError, SourceChanged):
            return None

    def _relocate(self, end_offset: int, first: _Located, content: Content, old: LongLineIndex | None) -> Location:
        """Row and column of `end_offset` after an edit that merged characters: the characters that start before it.

        The row is found in the table and its bytes up to `end_offset` are re-decoded when that is at most `RELOCATE_LIMIT` bytes.
        Otherwise, for a row that starts in the edited row, the old long index of that row gives an exact character boundary shortly before the edit and the
        decode starts there.
        When neither is possible (an inserted text over the limit, or a row that the table cannot resolve) the column is approximate: the characters of the
        inserted text counted on their own, added to the column of the edit.
        Such a column can be off by the few characters that merged with the neighbours.
        """
        if self._table.read(end_offset - 1, 2) == _CRLF:
            end_offset += 1  # the edit joined a CR and an LF: the location is after the LF
        found = self._table.row_at_offset(end_offset)
        approximate = (first.row + content.breaks, content.tail_chars if content.breaks else first.column + content.tail_chars)
        if found is None:
            return approximate
        row, span = found
        anchor, column = span.start, 0
        if end_offset - anchor > RELOCATE_LIMIT:
            if old is None or row != first.row or content.breaks:
                return approximate
            column = max(first.column - 2 * _MERGE_CONTEXT, 0)
            relative = old.try_char_to_byte(column)
            if relative is None or end_offset - (span.start + relative) > RELOCATE_LIMIT:
                return approximate
            anchor = span.start + relative
        text = self._table.read(anchor, end_offset - anchor + _MERGE_CONTEXT + 1).decode("utf-8", SURROGATE_ESCAPE)
        used = 0
        for char in text:
            if anchor + used >= end_offset:
                break
            used += utf8_len(char)
            column += 1
        return (row, column)

    def _after_edit(self, first_row: int, last_row: int, row_delta: int, replacement: LongLineIndex | None, patch: str | None) -> None:
        """Clear the caches from the row above the start row, re-key and retire the long indexes, patch the edited row (design 6.3).

        Rows below the edit keep their index under the key shifted by `row_delta`, the rows of the edit are retired (an index is cancelled and joined by
        the reaper) unless `replacement` is the spliced index of the single edited row, and the rows above stay.
        """
        floor = max(first_row - 1, 0)
        with self._lock:
            for cache in (self._ranges, self._texts):
                if cache:
                    for key in [key for key in cache if key >= floor]:
                        del cache[key]
            rekeyed: OrderedDict[int, LongLineIndex] = OrderedDict()
            for key, index in self._long.items():
                if key < first_row:
                    rekeyed[key] = index
                elif key > last_row:
                    rekeyed[key + row_delta] = index
                elif key == first_row and replacement is not None:
                    rekeyed[key] = replacement
                    index.cancel()
                    self._retired.append(index)
                else:
                    index.cancel()
                    self._retired.append(index)
            self._long = rekeyed
            if first_row <= 1:
                self._newline = None
            if patch is not None:
                self._texts[first_row] = patch
                self._seen_width = max(self._seen_width, advance_disp(patch, 0, self._config.tab_width))
            overflow = self._take_overflow()
            subscribers = list(self._subscribers)
            closed = self._closed
        if replacement is not None and not closed:
            for callback in subscribers:
                replacement.subscribe(callback)
            replacement.start()
        self._reap(overflow)

    def attach_syntax(self, syntax: SyntaxAwareDocument | None) -> None:
        """Delegate `prepare_query` and `query_syntax_tree` to `syntax`, a parse of the whole text used for highlighting (`None` detaches it).

        `replace_range` forwards every edit to it.
        """
        self._syntax = syntax

    @property
    def has_syntax(self) -> bool:
        """True when a syntax mirror is attached."""
        return self._syntax is not None

    def query_syntax_tree(
        self,
        query: Query,
        start_point: tuple[int, int] | None = None,
        end_point: tuple[int, int] | None = None,
    ) -> dict[str, list[Node]]:
        if self._syntax is None:
            return {}
        return self._syntax.query_syntax_tree(query, start_point, end_point)

    def prepare_query(self, query: str) -> Query | None:
        if self._syntax is None:
            return None
        return self._syntax.prepare_query(query)

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

    def row_display_width(self, row: int, tab_width: int = 4) -> int:
        """Display width of a short or medium row; decodes an uncached row once without adding it to the text cache (measuring only).

        Raises:
            WholeLineAccess: The row is long.
        """
        row = self._norm(row)
        with self._lock:
            cached = self._texts.get(row)
        if cached is not None:
            return advance_disp(cached, 0, tab_width)
        found = self._range(row)
        if found.content_end - found.start > self._config.long_row_threshold:
            self.call_log.refuse("row_display_width", row)
            msg = f"row_display_width on long row {row}"
            raise WholeLineAccess(msg)
        text = self.read_bytes(found.start, found.content_end - found.start, cache=False).decode("utf-8", SURROGATE_ESCAPE)
        self.call_log.record("row_display_width", row, len(text))
        return advance_disp(text, 0, tab_width)

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
