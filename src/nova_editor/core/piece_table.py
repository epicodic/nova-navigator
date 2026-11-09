"""The editable document as a piece table with row queries (ACT4 design 4.5 and 5, ACT1 design 5 to 8).

`PieceTable` holds the document as a `PieceTree` only.
Row numbers of the edited document come from per-piece break counts; the original file is never rescanned.
An original piece beyond the line scan is *unknown*: its break count is not known yet, and `_fill` resolves it once the scan has passed it.

Invariants:
- I1. The document is the concatenation of the tree pieces; `length` and `read` never consult the scan.
  Any offset in `[0, length]` is accepted by `splice` and `content`.
- I2. Only original-source pieces can be unknown; `breaks` and `row0` of an unknown piece are never read.
- I3. For every piece, known or not, `first_is_lf` and `last_is_cr` equal the bytes.
- I4. A known piece `[a, b)` of the live original satisfies `b <= F` at the time of its fill (`F` is the frontier of the scan), so its counts are exact.
- I5. After a public call that ran `_fill`, the first unknown piece in document order lies beyond the frontier (`a >= F`), is a legacy-generation piece,
  or the table is the identity before the scan completed, or the batch of `FILL_BATCH` pieces was used up, or a row exceeded the read budget.
- I6. The aggregate is the associative `combine` of the pieces; `lead_breaks <= breaks`, with equality when there is no unknown piece.
- I7. Rows `r < lead_breaks` are exact and resolvable; row `lead_breaks` is the open row while a piece is unknown.
  `snapshot().count` is `lead_breaks + 1` (a lower bound) while a piece is unknown, `complete` needs no unknown piece and a finished scan.
- I8. Two pieces that are contiguous in one source and equally known are merged; a known and an unknown piece never are.
- I9. A boundary between a CR and an LF is rejected with `ValueError`, also across pieces.
- Identity fast path: while the document is exactly the original file (one original piece, known or unknown, or both empty),
  `snapshot`, `row_range`, `lines` and `row_at_offset` return the answers of the `LineIndex` unchanged.
  Splicing the original bytes back (undo) restores the single piece and with it the fast path.
- Row counts are lower bounds while a piece is unknown or the scan is incomplete; a row that exceeds the per-call read budget of the index gives `None`.
- The table is meant for one thread (the UI thread).
  Its sources are thread-safe, so other threads may read through them.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from nova_editor.core.add_store import AddStore
from nova_editor.core.byte_source import ByteSource
from nova_editor.core.line_index import DEFAULT_MAX_LINES_PER_CALL, LineIndex, LineSnapshot, RowRange
from nova_editor.core.original_source import OriginalSource
from nova_editor.core.piece_tree import PieceTree
from nova_editor.core.pieces import (
    MAX_GENERATION,
    Content,
    Piece,
    PieceSource,
    RowNotIndexed,
    generation_of,
    make_piece,
    make_unknown_piece,
    merge_pieces,
    segment_of,
)
from nova_editor.core.row_source import RowSource

_CRLF = b"\r\n"

FILL_BATCH = 64
"""Largest number of unknown pieces that one call resolves, so a scan jump over many earlier edits cannot stall the UI thread."""


@dataclass(frozen=True, slots=True)
class _State:
    """Facts of one call: the scan snapshot and the tree aggregate."""

    snap: LineSnapshot
    length: int
    breaks: int
    lead_breaks: int
    unknown: int
    complete: bool  # no unknown piece and the scan finished (DEC-9 Q-B)


class PieceTable:
    """Editable byte document over an immutable original, an append-only add store and a `PieceTree`."""

    def __init__(self, source: ByteSource, line_index: LineIndex, add_store: AddStore, fanout: int = 32) -> None:
        """Create the table as one unknown original piece, so the identity fast path applies.

        Args:
            source: the original bytes; `line_index` must index this source.
            line_index: row index of the original (started or scanned by the caller).
            add_store: the store that holds every added byte (pieces with `src >= 1`).
            fanout: fan-out of the tree (32 in production, 4 in fuzz tests).
        """
        self._source = source
        self._index = line_index
        self._add_store = add_store
        self._original = OriginalSource(line_index, source)
        self._segments: dict[int, PieceSource] = {}
        self._legacy: list[tuple[OriginalSource, AddStore]] = []  # generation g is _legacy[g - 1]
        self._orig_length = source.length()
        self._tree = PieceTree(self._source_of, fanout)
        self._fill_key: tuple[int, bool, int, int, int, int] | None = None
        self._fill_frontier = 0
        self._fill_pending = False  # the last fill stopped early (batch used up or a row over the read budget)
        if self._orig_length > 0:
            # Two byte reads for the flags, now: the source may be closed later.
            first = make_unknown_piece(self._original, 0, 0, self._orig_length)
            self._tree = PieceTree.from_pieces(self._source_of, [first], fanout)

    # ----- sources -------------------------------------------------------------------------------------------------------------------

    def source_of(self, src: int) -> PieceSource:
        """Return the source that the piece source number `src` stands for (the original, an add segment or a legacy generation)."""
        return self._source_of(src)

    def _source_of(self, src: int) -> PieceSource:
        if src == 0:
            return self._original
        found = self._segments.get(src)
        if found is None:
            generation = generation_of(src)
            segment = segment_of(src)
            if generation == 0:
                found = self._add_store.segment(segment)
            else:
                original, store = self._legacy[generation - 1]
                found = original if segment == 0 else store.segment(segment)
            self._segments[src] = found
        return found

    def register_legacy(self, original: OriginalSource, add_store: AddStore) -> int:
        """Keep an old original and add store as a legacy generation and return its number (1 to 255).

        Pieces with `src = make_src(generation, k)` then resolve `k = 0` to `original` and `k >= 1` to a segment of `add_store`.

        Raises:
            ValueError: when 255 generations are registered already.
        """
        if len(self._legacy) >= MAX_GENERATION:
            raise ValueError(f"no free legacy generation (maximum {MAX_GENERATION})")
        self._legacy.append((original, add_store))
        return len(self._legacy)

    @property
    def legacy(self) -> tuple[tuple[OriginalSource, AddStore], ...]:
        """The registered legacy generations in order (generation `g` is element `g - 1`)."""
        return tuple(self._legacy)

    @property
    def tree(self) -> PieceTree:
        """The tree of the pieces (read access for tests and diagnostics)."""
        return self._tree

    @property
    def has_unknown(self) -> bool:
        """Whether a piece still has an unknown break count (it lies beyond the line scan, or was not filled yet)."""
        return self._tree.unknown > 0

    @property
    def has_open_tail(self) -> bool:
        """Deprecated alias of `has_unknown`."""
        return self.has_unknown

    # ----- state ---------------------------------------------------------------------------------------------------------------------

    def _refresh(self) -> LineSnapshot:
        """Take one snapshot of the original scan and resolve the unknown pieces the scan has passed."""
        snap = self._index.snapshot()
        self._fill(snap)
        return snap

    def _fill(self, snap: LineSnapshot) -> None:
        """Turn the first unknown pieces of the original that lie below the scan frontier into known pieces (at most `FILL_BATCH`).

        The first unknown piece in document order gates every row answer, so nothing is gained by filling later ones before it.
        A piece that crosses the frontier is split there; the frontier is a row start, never between a CR and an LF.
        A `RowNotIndexed` (a row over the read budget) leaves the piece unknown and the next call retries.
        """
        tree = self._tree
        if tree.unknown == 0 or (self._is_identity() and not snap.complete):
            return
        key = (snap.scanned_bytes, snap.complete, tree.piece_count, tree.length, tree.unknown, tree.lead_breaks)
        if key == self._fill_key:
            return
        frontier = self._orig_length if snap.complete else snap.scanned_bytes
        self._fill_frontier = frontier
        self._fill_pending = True
        for _ in range(FILL_BATCH):
            found = tree.first_unknown()
            if found is None or found[1].src != 0 or found[1].a >= frontier:
                self._fill_pending = False
                break
            index, piece, start = found
            if piece.b > frontier:
                tree.split_at(start + frontier - piece.a)
                found = tree.first_unknown()
                if found is None:
                    raise AssertionError("the split lost the unknown piece")
                index, piece, start = found
            try:
                known = self._known_piece(piece, snap, frontier)
            except RowNotIndexed:
                break
            tree.replace_piece(index, known)
            tree.merge_around(index)
        if not self._fill_pending:
            self._fill_key = (snap.scanned_bytes, snap.complete, tree.piece_count, tree.length, tree.unknown, tree.lead_breaks)

    def _known_piece(self, piece: Piece, snap: LineSnapshot, frontier: int) -> Piece:
        """Return `piece` with its break facts, from the snapshot (`piece.b <= frontier`).

        The row of the frontier is `snap.count - 1` (the open row); the original index cannot answer it.

        Raises:
            RowNotIndexed: when the index cannot resolve a row.
        """
        a, x = piece.a, piece.b
        row_a = 0 if a == 0 else self._original.row_of(a)
        row_x = snap.count - 1 if x == frontier else self._original.row_of(x)
        breaks = row_x - row_a
        if piece.last_is_cr and x < self._orig_length and self._original.read(x, x + 1) == b"\n":
            breaks += 1  # a CR that ends the piece counts on its own even when the original continues with an LF (see `make_piece`)
        return Piece(piece.src, a, x, breaks, row_a, piece.first_is_lf, piece.last_is_cr)

    def check_invariants(self) -> None:
        """Check I5 and I8 (and the tree structure); raise `AssertionError` on a violation.

        Call it right after a public method, while the scan does not move.
        """
        self._tree.check_invariants()
        previous: Piece | None = None
        for piece in self._tree.pieces():
            if previous is not None and merge_pieces(previous, piece) is not None:
                raise AssertionError(f"mergeable pieces: {previous} {piece}")
            previous = piece
        found = self._tree.first_unknown()
        if found is not None and not self._fill_pending and found[1].src == 0 and found[1].a < self._fill_frontier:
            raise AssertionError(f"unknown piece below the frontier {self._fill_frontier}: {found[1]}")

    def _state(self, snap: LineSnapshot) -> _State:
        aggregate = self._tree.aggregate
        return _State(snap, aggregate.length, aggregate.breaks, aggregate.lead_breaks, aggregate.unknown, aggregate.unknown == 0 and snap.complete)

    def _is_identity(self) -> bool:
        if self._orig_length == 0:
            return self._tree.length == 0
        if self._tree.piece_count != 1:
            return False
        piece = self._tree.piece_at(0)
        return piece.src == 0 and piece.a == 0 and piece.b == self._orig_length

    @property
    def is_identity(self) -> bool:
        """Whether the document is exactly the original (row queries then go straight to the `LineIndex`)."""
        self._refresh()
        return self._is_identity()

    @property
    def length(self) -> int:
        """Number of bytes of the document."""
        return self._tree.length

    def snapshot(self) -> LineSnapshot:
        """Return the row count, `complete`, the scan error and `scanned_bytes`.

        While a piece is unknown, `count` is `lead_breaks + 1`, a lower bound: the rows before the first unknown piece plus the open row.
        `complete` needs no unknown piece and a finished scan.
        `scanned_bytes` is in document coordinates: the start of the open row, or the length when no piece is unknown; offsets below it can be resolved.
        """
        snap = self._refresh()
        if self._is_identity():
            return snap
        state = self._state(snap)
        count = state.lead_breaks + 1 if state.unknown else state.breaks + 1
        scanned = state.length if state.unknown == 0 else self._start(state.lead_breaks, state) or 0
        return LineSnapshot(count, state.complete, snap.error, scanned)

    # ----- bytes ---------------------------------------------------------------------------------------------------------------------

    def read(self, offset: int, size: int) -> bytes:
        """Return up to `size` bytes from `offset` (shorter at the end of the document)."""
        self._refresh()
        if offset < 0 or size < 0:
            raise ValueError("offset and size must not be negative")
        end = min(offset + size, self.length)
        if end <= offset:
            return b""
        return self._read(offset, end)

    def _read(self, start: int, end: int) -> bytes:
        """Read `[start, end)`, which lies inside the document."""
        return self._tree.read(start, end)

    def iter_range(self, start: int, end: int) -> Iterator[tuple[Piece, int, int]]:
        """Yield `(piece, lo, hi)` for the pieces that overlap `[start, end)`; `lo..hi` is the overlap relative to the piece start.

        An unknown piece carries `breaks == 0`; read bytes through the piece's source.
        """
        self._refresh()
        if not 0 <= start <= end <= self.length:
            raise ValueError(f"range [{start}, {end}) outside 0..{self.length}")
        if start < end:
            yield from self._tree.iter_range(start, end)

    def layout_range(self, start: int, end: int) -> list[tuple[int, int, int]]:
        """Return the runs `(src, a, b)` that make up the document bytes `[start, end)`, in document order.

        No source is read and `SourceChanged` is never raised (the scan is not consulted, so unknown pieces stay as they are).

        Raises:
            ValueError: when the range is invalid.
        """
        if not 0 <= start <= end <= self.length:
            raise ValueError(f"range [{start}, {end}) outside 0..{self.length}")
        if start == end:
            return []
        return [(piece.src, piece.a + lo, piece.a + hi) for piece, lo, hi in self._tree.iter_range(start, end)]

    def layout_iter(self, start: int, end: int) -> Iterator[tuple[int, int, int]]:
        """Like `layout_range` but lazy; no `_refresh`, no reads, caller holds the document lock.

        Raises:
            ValueError: when the range is invalid (on the first step of the iteration).
        """
        if not 0 <= start <= end <= self.length:
            raise ValueError(f"range [{start}, {end}) outside 0..{self.length}")
        for piece, lo, hi in self._tree.iter_range(start, end):
            yield piece.src, piece.a + lo, piece.a + hi

    def row_source(self, start: int, end: int) -> RowSource:
        """Return a snapshot `ByteSource` of the document bytes `[start, end)`, with offsets relative to `start`.

        The pieces are captured now; later splices do not change it and other threads may read it (design 6.1).
        Call it on the table's thread: it resolves the piece sources here.
        """
        pieces = [piece._replace(a=piece.a + lo, b=piece.a + hi) for piece, lo, hi in self.iter_range(start, end)]
        return RowSource(pieces, self._source_of)

    # ----- editing -------------------------------------------------------------------------------------------------------------------

    def add(self, data: bytes) -> Content:
        """Append `data` to the add store and return it as `Content` (one piece; typing continues the tail segment, so the piece merges on splice).

        Raises:
            ValueError: when `data` is empty.
        """
        segment, a, b = self._add_store.append(data)
        piece = make_piece(self._source_of(segment), segment, a, b)
        cut = max(data.rfind(b"\r"), data.rfind(b"\n"))
        return Content.from_pieces([piece], len(data[cut + 1 :].decode("utf-8", "surrogateescape")))

    def content_bytes(self, content: Content, start: int, end: int) -> bytes:
        """Return the bytes `[start, end)` of `content` (offsets relative to the content)."""
        parts: list[bytes] = []
        position = 0
        for piece in content.pieces:
            lo = max(start - position, 0)
            hi = min(end - position, piece.length)
            if lo < hi:
                parts.append(self._source_of(piece.src).read(piece.a + lo, piece.a + hi))
            position += piece.length
            if position >= end:
                break
        return b"".join(parts)

    def byte_content(self, offset: int) -> Content:
        """Return the single byte at `offset` as `Content` (one piece, no data read); unlike `content` the byte may be half of a CRLF.

        A byte the line scan has not reached is an unknown piece.

        Raises:
            ValueError: when `offset` is outside the document.
        """
        for piece, lo, _ in self.iter_range(offset, offset + 1):
            source, a = self._source_of(piece.src), piece.a + lo
            try:
                sub = make_piece(source, piece.src, a, a + 1)
            except RowNotIndexed:
                sub = make_unknown_piece(source, piece.src, a, a + 1)
            return Content.from_pieces([sub], 0)
        raise ValueError(f"offset {offset} outside 0..{self.length}")

    def content(self, start: int, end: int, tail_chars: int | None = None) -> Content:
        """Return the bytes `[start, end)` as `Content` without reading any data (the pieces may be split at the two boundaries).

        `tail_chars` is the number of characters after the last break of the range when the caller knows it (the data is read to count them otherwise,
        capped when a piece is unknown).
        Any range inside the document is accepted, whether or not the scan has reached it.

        Raises:
            ValueError: when the range is invalid or a boundary is between a CR and an LF.
            RowNotIndexed: when a known piece must be split and its row cannot be resolved within the read budget.
        """
        self._refresh()
        self._prepare(start, end)
        result = self._tree.content(start, end, tail_chars)
        self._after_change()
        return result

    def splice(self, start: int, end: int, content: Content, removed_tail_chars: int | None = None) -> Content:
        """Replace the bytes `[start, end)` by `content` and return the removed bytes as `Content`.

        `removed_tail_chars` is the `tail_chars` of the removed bytes when the caller knows it (the data is read to count them otherwise).

        `content` holds pieces of the add store or pieces taken from earlier `content` or `splice` results.
        Any range inside the document is accepted, whether or not the scan has reached it.
        Nothing changes when an error is raised.

        Raises:
            ValueError: when the range is invalid or a boundary is between a CR and an LF.
            RowNotIndexed: when a known piece must be split and its row cannot be resolved within the read budget.
        """
        self._refresh()
        self._prepare(start, end)
        result = self._tree.splice(start, end, content, removed_tail_chars)
        self._after_change()
        return result

    def _prepare(self, start: int, end: int) -> None:
        """Validate the range and both boundaries."""
        if not 0 <= start <= end <= self.length:
            raise ValueError(f"range [{start}, {end}) outside 0..{self.length}")
        self._tree.check_boundary(start)
        self._tree.check_boundary(end)

    def _after_change(self) -> None:
        """Resolve unknown pieces that a splice brought in (stale contents of the scanned range)."""
        self._fill_key = None
        self._fill(self._index.snapshot())

    # ----- rows ----------------------------------------------------------------------------------------------------------------------

    def row_range(self, row: int) -> RowRange | None:
        """Return the byte range of `row`, or `None` when it is not scanned yet or cannot be resolved within the read budget.

        Raises:
            IndexError: when `row` is negative.
        """
        snap = self._refresh()
        if self._is_identity():
            return self._index.row_range(row)
        if row < 0:
            raise IndexError(row)
        return self._range(row, self._state(snap))

    def lines(self, first: int, count: int) -> list[RowRange]:
        """Return up to `min(count, 128)` consecutive resolvable rows from `first` (a shorter list is a prefix, as for `LineIndex.lines`)."""
        snap = self._refresh()
        if self._is_identity():
            return self._index.lines(first, count)
        if first < 0:
            raise IndexError(first)
        if count <= 0:
            return []
        state = self._state(snap)
        stop = min(self._resolvable(state), first + min(count, DEFAULT_MAX_LINES_PER_CALL))
        result: list[RowRange] = []
        begin = self._start(first, state) if first < stop else None
        for row in range(first, stop):
            if begin is None:
                break
            end = self._start(row + 1, state) if row < state.breaks else state.length
            if end is None:
                break
            result.append(self._make_range(begin, end, is_last=row == state.breaks))
            begin = end
        return result

    def row_at_offset(self, offset: int) -> tuple[int, RowRange] | None:
        """Return `(row, range)` of the row containing byte `offset`; terminator bytes belong to their row.

        `offset == length` maps to the last row once the document is complete.
        Returns `None` for a negative or too large offset, an offset in or behind the first unknown piece or in the open row,
        or a walk over the read budget.
        """
        snap = self._refresh()
        if self._is_identity():
            return self._index.row_at_offset(offset)
        state = self._state(snap)
        if offset < 0 or offset > state.length:
            return None
        if offset == state.length:
            if not state.complete:
                return None
            row = state.breaks
        else:
            try:
                row = self._tree.row_of(offset)
            except RowNotIndexed:
                return None
        found = self._range(row, state)
        return None if found is None else (row, found)

    def _resolvable(self, state: _State) -> int:
        return state.breaks + 1 if state.complete else state.lead_breaks

    def _range(self, row: int, state: _State) -> RowRange | None:
        if row >= self._resolvable(state):
            return None
        begin = self._start(row, state)
        end = self._start(row + 1, state) if row < state.breaks else state.length
        if begin is None or end is None:
            return None
        return self._make_range(begin, end, is_last=row == state.breaks)

    def _make_range(self, begin: int, end: int, *, is_last: bool) -> RowRange:
        terminator = 0 if is_last else self._terminator_length(end)
        return RowRange(begin, end - terminator, end)

    def _terminator_length(self, end: int) -> int:
        """Return the terminator length of the row that ends at `end`, from at most two bytes before it."""
        tail = self._read(max(end - 2, 0), end)
        return 2 if tail.endswith(_CRLF) else 1 if tail[-1:] in (b"\n", b"\r") else 0

    def _start(self, row: int, state: _State) -> int | None:
        """Return the start of `row` (the end of the `row`-th break), or `None` when it cannot be resolved."""
        if row == 0:
            return 0
        if row <= state.lead_breaks:
            return self._tree.find_break(row)
        return None
