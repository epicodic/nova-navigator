"""The editable document as a piece table with row queries (ACT4 design 4.5 and 5).

`PieceTable` holds the document as a `PieceTree` plus, while the scan of the original is incomplete, the *open tail*.
Row numbers of the edited document come from per-piece break counts; the original file is never rescanned.

Invariants:
- The document is the bytes of the tree pieces followed by the bytes `[tail.a, L)` of the original (when there is a tail), `L` being the original length.
- The tail exists only while the scan of the original is incomplete.
  Its break count is not final, so the tree never contains it.
  Row starts inside the tail come from the original `LineIndex` (`tail.row0` is the original row that contains `tail.a`).
- A tail is cut off at `x` only when `x < scanned_bytes` of the original, i.e. when the original index can resolve the row of `x`.
  Splices and `content` calls whose end lies in the tail at an unscanned position raise `RowNotIndexed` and change nothing.
  Row queries never raise for unscanned rows; they return `None`.
- The first call after the scan completed folds the tail into the tree with exact counts (no index lookup is needed).
- Boundaries between a CR and an LF (also across the tree/tail junction) are rejected with `ValueError`.
- Identity fast path: while the document is exactly the original file (one original piece, or only the open tail, or both empty),
  `snapshot`, `row_range`, `lines` and `row_at_offset` return the answers of the `LineIndex` unchanged.
  Splicing the original bytes back (undo) restores the single piece and with it the fast path.
- Row counts are lower bounds while the scan is incomplete.
  Row `r` is resolvable when `r < count - 1` (incomplete) or `r < count` (complete); a row of the original that exceeds the per-call read budget of the index gives `None`.
- The table is meant for one thread (the UI thread).
  Its sources are thread-safe, so other threads may read through them.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import NamedTuple

from nova_editor.core.add_store import AddStore
from nova_editor.core.byte_source import ByteSource
from nova_editor.core.line_index import DEFAULT_MAX_LINES_PER_CALL, LineIndex, LineSnapshot, RowRange
from nova_editor.core.original_source import OriginalSource, RowNotIndexed
from nova_editor.core.piece_tree import PieceTree
from nova_editor.core.pieces import MAX_GENERATION, Content, Piece, PieceSource, generation_of, make_piece, segment_of
from nova_editor.core.row_source import RowSource

_CRLF = b"\r\n"


class _Tail(NamedTuple):
    """The open tail `[a, L)` of the original; `row0` is the original row that contains byte `a`."""

    a: int
    row0: int
    first_is_lf: bool


@dataclass(frozen=True, slots=True)
class _State:
    """Facts of one call: the tree aggregate, the tail, the junction flag and the known tail breaks."""

    snap: LineSnapshot
    tail: _Tail | None
    tree_length: int
    tree_breaks: int
    tree_last_is_cr: bool
    tail_known: int  # breaks of the tail read on its own that are final (0 without a tail)

    @property
    def junction(self) -> bool:
        """The tree ends in CR and the tail starts with LF: they are one CRLF."""
        return self.tail is not None and self.tree_last_is_cr and self.tail.first_is_lf

    @property
    def breaks(self) -> int:
        """Number of known breaks of the document."""
        return self.tree_breaks + self.tail_known - self.junction

    @property
    def complete(self) -> bool:
        return self.tail is None


class PieceTable:
    """Editable byte document over an immutable original, an append-only add store and a `PieceTree`."""

    def __init__(self, source: ByteSource, line_index: LineIndex, add_store: AddStore, fanout: int = 32) -> None:
        """Create the table as one original piece (the open tail), so the identity fast path applies.

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
        first_is_lf = self._orig_length > 0 and source.read(0, 1) == b"\n"
        self._last_is_cr = self._orig_length > 0 and source.read(self._orig_length - 1, 1) == b"\r"  # read now: the source may be closed at fold time
        self._tail: _Tail | None = _Tail(0, 0, first_is_lf) if self._orig_length > 0 else None

    # ----- sources -------------------------------------------------------------------------------------------------------------------

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
    def tree(self) -> PieceTree:
        """The tree of the pieces before the open tail (read access for tests and diagnostics)."""
        return self._tree

    @property
    def has_open_tail(self) -> bool:
        """Whether the last original piece is still outside the tree (the scan was incomplete at the last call)."""
        return self._tail is not None

    # ----- state ---------------------------------------------------------------------------------------------------------------------

    def _refresh(self) -> LineSnapshot:
        """Take one snapshot of the original scan and fold the tail into the tree when the scan completed."""
        snap = self._index.snapshot()
        tail = self._tail
        if tail is not None and snap.complete:
            last = self._orig_length
            piece = Piece(0, tail.a, last, snap.count - 1 - tail.row0, tail.row0, tail.first_is_lf, self._last_is_cr)
            self._tree.splice(self._tree.length, self._tree.length, Content.from_pieces([piece], 0))
            self._tail = None
        return snap

    def _state(self, snap: LineSnapshot) -> _State:
        tail = self._tail
        aggregate = self._tree.aggregate
        known = 0 if tail is None else max(snap.count - 1 - tail.row0, 0)
        return _State(snap, tail, aggregate.length, aggregate.breaks, aggregate.last_is_cr, known)

    def _scanned(self, state: _State) -> int:
        """Return `scanned_bytes` in document coordinates: the start of the open row, the first row that is not terminated yet."""
        tail = state.tail
        if tail is None:
            return self.length
        if state.tail_known > 0:
            return state.tree_length + state.snap.scanned_bytes - tail.a  # the open row lies in the tail
        found = self._start(state.breaks, state)  # the open row starts in the tree
        return 0 if found is None else found

    def _is_identity(self) -> bool:
        tail = self._tail
        if tail is not None:
            return tail.a == 0 and self._tree.length == 0
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
        tail = self._tail
        return self._tree.length + (0 if tail is None else self._orig_length - tail.a)

    def snapshot(self) -> LineSnapshot:
        """Return the row count (a lower bound until the original scan completes), `complete`, the scan error and `scanned_bytes`.

        `scanned_bytes` is in document coordinates; offsets below it can be edited and resolved.
        """
        snap = self._refresh()
        if self._is_identity():
            return snap
        state = self._state(snap)
        return LineSnapshot(state.breaks + 1, state.complete, snap.error, self.length if state.tail is None else self._scanned(state))

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
        tail = self._tail
        tree_length = self._tree.length
        parts: list[bytes] = []
        if start < tree_length:
            parts.append(self._tree.read(start, min(end, tree_length)))
        if tail is not None and end > tree_length:
            begin = tail.a + max(start - tree_length, 0)
            parts.append(self._source.read(begin, tail.a + end - tree_length - begin))
        return b"".join(parts)

    def iter_range(self, start: int, end: int) -> Iterator[tuple[Piece, int, int]]:
        """Yield `(piece, lo, hi)` for the pieces that overlap `[start, end)`; `lo..hi` is the overlap relative to the piece start.

        The open tail is yielded as an original piece whose `breaks` is only the final part; read bytes through the piece's source.
        """
        self._refresh()
        if not 0 <= start <= end <= self.length:
            raise ValueError(f"range [{start}, {end}) outside 0..{self.length}")
        tree_length = self._tree.length
        if start < min(end, tree_length):
            yield from self._tree.iter_range(start, min(end, tree_length))
        tail = self._tail
        if tail is not None and end > tree_length and end > start:
            last_is_cr = self._source.read(self._orig_length - 1, 1) == b"\r"
            piece = Piece(0, tail.a, self._orig_length, 0, tail.row0, tail.first_is_lf, last_is_cr)
            yield piece, max(start - tree_length, 0), end - tree_length

    def layout_range(self, start: int, end: int) -> list[tuple[int, int, int]]:
        """Return the runs `(src, a, b)` that make up the document bytes `[start, end)`, in document order.

        No source is read and `SourceChanged` is never raised; the open tail is its own run of the original.

        Raises:
            ValueError: when the range is invalid.
        """
        self._refresh()
        if not 0 <= start <= end <= self.length:
            raise ValueError(f"range [{start}, {end}) outside 0..{self.length}")
        tree_length = self._tree.length
        runs: list[tuple[int, int, int]] = []
        if start < min(end, tree_length):
            runs.extend((piece.src, piece.a + lo, piece.a + hi) for piece, lo, hi in self._tree.iter_range(start, min(end, tree_length)))
        tail = self._tail
        if tail is not None and end > tree_length and end > start:
            runs.append((0, tail.a + max(start - tree_length, 0), tail.a + end - tree_length))
        return runs

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

        Raises:
            ValueError: when `offset` is outside the document.
        """
        for piece, lo, _ in self.iter_range(offset, offset + 1):
            sub = make_piece(self._source_of(piece.src), piece.src, piece.a + lo, piece.a + lo + 1)
            return Content.from_pieces([sub], 0)
        raise ValueError(f"offset {offset} outside 0..{self.length}")

    def content(self, start: int, end: int, tail_chars: int | None = None) -> Content:
        """Return the bytes `[start, end)` as `Content` without reading any data (the pieces may be split at the two boundaries).

        `tail_chars` is the number of characters after the last break of the range when the caller knows it (the data is read to count them otherwise).

        Raises:
            ValueError: when the range is invalid or a boundary is between a CR and an LF.
            RowNotIndexed: when `end` lies in the open tail beyond the scanned part, or its row cannot be resolved within the budget.
        """
        snap = self._refresh()
        self._prepare(start, end, snap)
        return self._tree.content(start, end, tail_chars)

    def splice(self, start: int, end: int, content: Content, removed_tail_chars: int | None = None) -> Content:
        """Replace the bytes `[start, end)` by `content` and return the removed bytes as `Content`.

        `removed_tail_chars` is the `tail_chars` of the removed bytes when the caller knows it (the data is read to count them otherwise).

        `content` holds pieces of the add store or pieces taken from earlier `content` or `splice` results.
        Nothing changes (except an invisible move of tail bytes into the tree) when an error is raised.

        Raises:
            ValueError: when the range is invalid or a boundary is between a CR and an LF.
            RowNotIndexed: when `end` lies in the open tail beyond the scanned part, or its row cannot be resolved within the budget.
        """
        snap = self._refresh()
        self._prepare(start, end, snap)
        return self._tree.splice(start, end, content, removed_tail_chars)

    def _prepare(self, start: int, end: int, snap: LineSnapshot) -> None:
        """Validate the range and move the tail bytes before `end` into the tree."""
        if not 0 <= start <= end <= self.length:
            raise ValueError(f"range [{start}, {end}) outside 0..{self.length}")
        self._check_boundary(start)
        self._check_boundary(end)
        self._open(end, snap)

    def _check_boundary(self, offset: int) -> None:
        tail = self._tail
        tree_length = self._tree.length
        if tail is None or offset < tree_length:
            self._tree.check_boundary(offset)
            return
        if offset == tree_length:
            self._tree.check_boundary(offset)
            inside = self._tree.aggregate.last_is_cr and tail.first_is_lf
        else:
            x = tail.a + offset - tree_length
            inside = self._source.read(x - 1, 2) == _CRLF
        if inside:
            raise ValueError(f"offset {offset} is inside a CRLF")

    def _open(self, end: int, snap: LineSnapshot) -> None:
        """Make `end` a position inside the tree by cutting `[tail.a, x)` off the tail (exact counts from the original index)."""
        tail = self._tail
        tree_length = self._tree.length
        if tail is None or end <= tree_length:
            return
        x = tail.a + end - tree_length
        if x >= snap.scanned_bytes:
            raise RowNotIndexed(f"offset {end} is not scanned yet")
        row = self._original.row_of(x)
        piece = Piece(0, tail.a, x, row - tail.row0, tail.row0, tail.first_is_lf, self._source.read(x - 1, 1) == b"\r")
        self._tree.splice(tree_length, tree_length, Content.from_pieces([piece], 0))
        self._tail = _Tail(x, row, self._source.read(x, 1) == b"\n")

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
            end = self._start(row + 1, state) if row < state.breaks else self.length
            if end is None:
                break
            result.append(self._make_range(begin, end, is_last=row == state.breaks))
            begin = end
        return result

    def row_at_offset(self, offset: int) -> tuple[int, RowRange] | None:
        """Return `(row, range)` of the row containing byte `offset`; terminator bytes belong to their row.

        `offset == length` maps to the last row once the scan is complete.
        Returns `None` for a negative or too large offset, an offset that is not scanned yet, or a walk over the read budget.
        """
        snap = self._refresh()
        if self._is_identity():
            return self._index.row_at_offset(offset)
        state = self._state(snap)
        length = self.length
        if offset < 0 or offset > length:
            return None
        try:
            if offset == length:
                if not state.complete:
                    return None
                row = state.breaks
            elif state.tail is not None and offset >= state.tree_length:
                x = state.tail.a + offset - state.tree_length
                if offset >= self._scanned(state):
                    return None
                row = state.tree_breaks - state.junction + self._original.row_of(x) - state.tail.row0
            else:
                row = self._tree.row_of(offset)
        except RowNotIndexed:
            return None
        found = self._range(row, state)
        return None if found is None else (row, found)

    def _resolvable(self, state: _State) -> int:
        return state.breaks + 1 if state.complete else state.breaks

    def _range(self, row: int, state: _State) -> RowRange | None:
        if row >= self._resolvable(state):
            return None
        begin = self._start(row, state)
        end = self._start(row + 1, state) if row < state.breaks else self.length
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
        tail = state.tail
        if row <= state.tree_breaks:
            found = self._tree.find_break(row)
            if found is not None and found == state.tree_length and tail is not None and state.tree_last_is_cr and tail.first_is_lf:
                found += 1  # the CR at the end of the tree and the LF at the start of the tail are one CRLF
            return found
        if tail is None:
            return None
        rank = row - state.tree_breaks + state.junction
        if rank > state.tail_known:
            return None
        # The start of a row is the end of the previous row; the row that starts at the open row is not resolvable by the index.
        previous = self._index.row_range(tail.row0 + rank - 1)
        return None if previous is None else state.tree_length + previous.end - tail.a
