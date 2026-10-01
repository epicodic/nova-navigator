"""Counted B+ tree of pieces (ACT4 design 4.2).

Leaves hold pieces in parallel `array` columns (37 bytes per piece plus node overhead); `Piece` objects exist only while they are accessed.
Every node carries the piece count and the aggregate of its run, so offsets and breaks are found in O(log n).
`splice` costs O(log n) plus the number of pieces it removes or inserts.
"""

from __future__ import annotations

import re
from array import array
from collections.abc import Iterable, Iterator
from typing import NamedTuple

from nova_editor.core.pieces import (
    EMPTY_AGGREGATE,
    Aggregate,
    Content,
    Piece,
    SourceOf,
    combine,
    merge_pieces,
    tail_chars_of,
)

_BREAK = re.compile(rb"\r\n|\n|\r")
_MIN_FANOUT = 4
_TWO = 2
_FIRST_LF = 1
_LAST_CR = 2

type _Row = tuple[int, int, int, int, int, int]


class Location(NamedTuple):
    """Result of `PieceTree.find_offset`.

    `piece` is the piece that contains the byte at the offset (the one that starts there when the offset is a boundary); it is `None` at the very end.
    """

    index: int
    piece: Piece | None
    start: int
    inner: int


def _to_row(piece: Piece) -> _Row:
    flags = (_FIRST_LF if piece.first_is_lf else 0) | (_LAST_CR if piece.last_is_cr else 0)
    return (piece.src, piece.a, piece.b, piece.breaks, piece.row0, flags)


class _Leaf:
    """Up to `fanout` pieces in parallel columns."""

    __slots__ = ("a", "agg", "b", "brk", "count", "flg", "row0", "src")

    def __init__(self) -> None:
        self.src = array("I")
        self.a = array("Q")
        self.b = array("Q")
        self.brk = array("Q")
        self.row0 = array("Q")
        self.flg = array("B")
        self.count = 0
        self.agg = EMPTY_AGGREGATE

    @property
    def cols(self) -> tuple[array[int], ...]:
        return (self.src, self.a, self.b, self.brk, self.row0, self.flg)

    def size(self) -> int:
        return len(self.src)

    def piece(self, i: int) -> Piece:
        flags = self.flg[i]
        return Piece(self.src[i], self.a[i], self.b[i], self.brk[i], self.row0[i], bool(flags & _FIRST_LF), bool(flags & _LAST_CR))

    def row(self, i: int) -> _Row:
        return (self.src[i], self.a[i], self.b[i], self.brk[i], self.row0[i], self.flg[i])

    def insert_row(self, i: int, row: _Row) -> None:
        for col, value in zip(self.cols, row, strict=True):
            col.insert(i, value)

    def set_row(self, i: int, row: _Row) -> None:
        for col, value in zip(self.cols, row, strict=True):
            col[i] = value

    def delete_row(self, i: int) -> None:
        for col in self.cols:
            del col[i]

    def split_half(self) -> _Leaf:
        """Move the upper half into a new leaf (both are left unrefreshed)."""
        mid = self.size() // 2
        new = _Leaf()
        for old_col, new_col in zip(self.cols, new.cols, strict=True):
            new_col.extend(old_col[mid:])
            del old_col[mid:]
        return new

    def absorb(self, other: _Leaf) -> None:
        for col, other_col in zip(self.cols, other.cols, strict=True):
            col.extend(other_col)

    def refresh(self) -> None:
        agg = EMPTY_AGGREGATE
        for i in range(len(self.src)):
            flags = self.flg[i]
            agg = combine(agg, Aggregate(self.b[i] - self.a[i], self.brk[i], bool(flags & _FIRST_LF), bool(flags & _LAST_CR)))
        self.agg = agg
        self.count = len(self.src)


class _Inner:
    """Up to `fanout` children."""

    __slots__ = ("agg", "children", "count")

    def __init__(self, children: list[_Node]) -> None:
        self.children = children
        self.count = 0
        self.agg = EMPTY_AGGREGATE
        self.refresh()

    def size(self) -> int:
        return len(self.children)

    def refresh(self) -> None:
        agg = EMPTY_AGGREGATE
        count = 0
        for child in self.children:
            agg = combine(agg, child.agg)
            count += child.count
        self.agg = agg
        self.count = count


type _Node = _Leaf | _Inner


def _move(source: _Node, target: _Node, *, from_front: bool) -> None:
    """Move one item from `source` to the adjacent `target` (same kind); `from_front` means source is the right neighbour."""
    if isinstance(source, _Leaf) and isinstance(target, _Leaf):
        i = 0 if from_front else source.size() - 1
        row = source.row(i)
        source.delete_row(i)
        target.insert_row(target.size() if from_front else 0, row)
    elif isinstance(source, _Inner) and isinstance(target, _Inner):
        child = source.children.pop(0 if from_front else -1)
        if from_front:
            target.children.append(child)
        else:
            target.children.insert(0, child)
    else:
        raise TypeError("nodes of different heights")
    source.refresh()
    target.refresh()


def _absorb(left: _Node, right: _Node) -> None:
    if isinstance(left, _Leaf) and isinstance(right, _Leaf):
        left.absorb(right)
    elif isinstance(left, _Inner) and isinstance(right, _Inner):
        left.children.extend(right.children)
    else:
        raise TypeError("nodes of different heights")
    left.refresh()


def _split_even[T](items: list[T], fanout: int) -> list[list[T]]:
    """Cut `items` into the fewest runs of at most `fanout`, of nearly equal size (each at least `fanout // 2`)."""
    count = -(-len(items) // fanout)
    base, extra = divmod(len(items), count)
    runs: list[list[T]] = []
    pos = 0
    for i in range(count):
        size = base + (1 if i < extra else 0)
        runs.append(items[pos : pos + size])
        pos += size
    return runs


class PieceTree:
    """The document as a sequence of pieces in a counted B+ tree.

    Offsets are byte offsets into the concatenation of the pieces.
    A boundary between a CR and an LF (inside one terminator) is never allowed: `split_at`, `splice` and `content` raise `ValueError`.
    """

    def __init__(self, source_of: SourceOf, fanout: int = 32) -> None:
        """Create an empty tree.

        Args:
            source_of: maps a piece's `src` to its `PieceSource`.
            fanout: maximum items per node (at least 4; 32 in production, 4 in fuzz tests).
        """
        if fanout < _MIN_FANOUT:
            raise ValueError(f"fanout must be at least {_MIN_FANOUT}")
        self._source_of = source_of
        self._fanout = fanout
        self._min = fanout // 2
        self._root: _Node = _Leaf()

    @classmethod
    def from_pieces(cls, source_of: SourceOf, pieces: Iterable[Piece], fanout: int = 32) -> PieceTree:
        """Build a tree bottom-up from `pieces` (adjacent contiguous pieces are merged)."""
        tree = cls(source_of, fanout)
        merged: list[Piece] = []
        for piece in pieces:
            joined = merge_pieces(merged[-1], piece) if merged else None
            if joined is None:
                merged.append(piece)
            else:
                merged[-1] = joined
        if not merged:
            return tree
        level: list[_Node] = []
        for run in _split_even(merged, fanout):
            leaf = _Leaf()
            for piece in run:
                leaf.insert_row(leaf.size(), _to_row(piece))
            leaf.refresh()
            level.append(leaf)
        while len(level) > 1:
            level = [_Inner(run) for run in _split_even(level, fanout)]
        tree._root = level[0]
        return tree

    # ----- sizes ---------------------------------------------------------------------------------------------------------------------

    @property
    def length(self) -> int:
        """Number of bytes in the document."""
        return self._root.agg.length

    @property
    def breaks(self) -> int:
        """Number of terminators in the document (CRLF counts once, also across piece junctions)."""
        return self._root.agg.breaks

    @property
    def piece_count(self) -> int:
        """Number of pieces."""
        return self._root.count

    @property
    def aggregate(self) -> Aggregate:
        """The aggregate of the whole document."""
        return self._root.agg

    # ----- navigation ----------------------------------------------------------------------------------------------------------------

    def piece_at(self, index: int) -> Piece:
        """Return the piece with number `index`."""
        if not 0 <= index < self._root.count:
            raise IndexError(index)
        node = self._root
        while isinstance(node, _Inner):
            for child in node.children:
                if index < child.count:
                    node = child
                    break
                index -= child.count
        return node.piece(index)

    def pieces(self) -> Iterator[Piece]:
        """Yield all pieces in order."""
        return self._walk(self._root, 0)

    def _walk(self, node: _Node, index: int) -> Iterator[Piece]:
        if isinstance(node, _Leaf):
            for i in range(index, node.size()):
                yield node.piece(i)
            return
        base = 0
        for child in node.children:
            if index < base + child.count:
                yield from self._walk(child, max(index - base, 0))
            base += child.count

    def find_offset(self, offset: int) -> Location:
        """Return the piece that contains the byte at `offset` (`piece` is `None` for `offset == length`)."""
        if not 0 <= offset <= self.length:
            raise ValueError(f"offset {offset} outside 0..{self.length}")
        if offset == self.length:
            return Location(self._root.count, None, offset, 0)
        node = self._root
        index = 0
        start = 0
        while isinstance(node, _Inner):
            for child in node.children:
                if offset < start + child.agg.length:
                    node = child
                    break
                start += child.agg.length
                index += child.count
        for i in range(node.size()):
            length = node.b[i] - node.a[i]
            if offset < start + length:
                return Location(index + i, node.piece(i), start, offset - start)
            start += length
        raise AssertionError("aggregate does not match the leaf")

    def iter_range(self, start: int, end: int) -> Iterator[tuple[Piece, int, int]]:
        """Yield `(piece, lo, hi)` for the pieces that overlap `[start, end)`; `lo..hi` is the overlap relative to the piece start."""
        self._check_range(start, end)
        if start == end:
            return
        location = self.find_offset(start)
        pos = location.start
        for piece in self._walk(self._root, location.index):
            if pos >= end:
                break
            yield piece, max(start - pos, 0), min(end - pos, piece.length)
            pos += piece.length

    def read(self, start: int, end: int) -> bytes:
        """Return the bytes `[start, end)` of the document."""
        return b"".join(self._source_of(piece.src).read(piece.a + lo, piece.a + hi) for piece, lo, hi in self.iter_range(start, end))

    def find_break(self, k: int) -> int | None:
        """Return the offset just after the `k`-th break (1-based), i.e. the start of row `k`.

        Returns `None` when the source cannot place the break (its `row_start` is `None`).

        Raises:
            IndexError: when `k` is not in `1..breaks`.
        """
        if not 1 <= k <= self.breaks:
            raise IndexError(k)
        node = self._root
        acc = EMPTY_AGGREGATE
        index = 0
        while isinstance(node, _Inner):
            for child in node.children:
                joined = combine(acc, child.agg)
                if joined.breaks >= k:
                    node = child
                    break
                acc = joined
                index += child.count
        piece: Piece | None = None
        for i in range(node.size()):
            candidate = node.piece(i)
            joined = combine(acc, candidate.aggregate)
            if joined.breaks >= k:
                piece = candidate
                index += i
                break
            acc = joined
        if piece is None:
            raise AssertionError("aggregate does not match the leaf")
        lead = acc.last_is_cr and piece.first_is_lf
        rank = k - acc.breaks + lead
        piece_end = acc.length + piece.length
        if rank == piece.breaks and piece.last_is_cr:
            # The last break is the CR at the piece end, or the first half of a CRLF that continues in the next piece.
            following = self.piece_at(index + 1) if index + 1 < self.piece_count else None
            return piece_end + (1 if following is not None and following.first_is_lf else 0)
        row_start = self._source_of(piece.src).row_start(piece.row0 + rank)
        if row_start is None:
            return None
        return acc.length + row_start - piece.a

    def row_of(self, offset: int) -> int:
        """Return the row, counted from 0, that contains the byte at `offset` (terminator bytes belong to their row).

        For `offset == length` this is the row after the last break.
        An offset between a CR and an LF gives the row that the CRLF ends.
        """
        if not 0 <= offset <= self.length:
            raise ValueError(f"offset {offset} outside 0..{self.length}")
        if offset == self.length:
            return self.breaks
        node = self._root
        acc = EMPTY_AGGREGATE
        start = 0
        while isinstance(node, _Inner):
            for child in node.children:
                if offset < start + child.agg.length:
                    node = child
                    break
                acc = combine(acc, child.agg)
                start += child.agg.length
        for i in range(node.size()):
            piece = node.piece(i)
            if offset < start + piece.length:
                inner = offset - start
                lead = acc.last_is_cr and piece.first_is_lf
                if inner == 0:
                    return acc.breaks - lead
                left = self._source_of(piece.src).breaks_between(piece.a, piece.a + inner)
                return acc.breaks + left - lead
            acc = combine(acc, piece.aggregate)
            start += piece.length
        raise AssertionError("aggregate does not match the leaf")

    # ----- boundaries ----------------------------------------------------------------------------------------------------------------

    def check_boundary(self, offset: int) -> None:
        """Raise `ValueError` when `offset` is out of range or between a CR and an LF."""
        location = self.find_offset(offset)
        if location.piece is None or offset == 0:
            return
        if location.inner == 0:
            previous = self.piece_at(location.index - 1)
            inside = previous.last_is_cr and location.piece.first_is_lf
        else:
            x = location.piece.a + location.inner
            inside = self._source_of(location.piece.src).read(x - 1, x + 1) == b"\r\n"
        if inside:
            raise ValueError(f"offset {offset} is inside a CRLF")

    def split_at(self, offset: int) -> int:
        """Make `offset` a piece boundary and return the index of the piece that starts there (`piece_count` at the end).

        Raises:
            ValueError: when `offset` is out of range or between a CR and an LF.
        """
        self.check_boundary(offset)
        location = self.find_offset(offset)
        piece = location.piece
        if piece is None or location.inner == 0:
            return location.index
        x = piece.a + location.inner
        source = self._source_of(piece.src)
        around = source.read(x - 1, x + 1)
        left_breaks = source.breaks_between(piece.a, x)
        left = Piece(piece.src, piece.a, x, left_breaks, piece.row0, piece.first_is_lf, around[:1] == b"\r")
        right = Piece(piece.src, x, piece.b, piece.breaks - left_breaks, piece.row0 + left_breaks, around[1:2] == b"\n", piece.last_is_cr)
        self._replace_at(self._root, location.index, _to_row(left))
        self._insert_piece(location.index + 1, right)
        return location.index + 1

    # ----- editing -------------------------------------------------------------------------------------------------------------------

    def content(self, start: int, end: int) -> Content:
        """Return the bytes `[start, end)` as `Content` (no bytes are copied); may split pieces at the two boundaries."""
        self._check_range(start, end)
        self.check_boundary(start)
        self.check_boundary(end)
        first = self.split_at(start)
        last = self.split_at(end)
        pieces = list(self._walk_count(first, last - first))
        self._try_merge(last)
        self._try_merge(first)
        return Content.from_pieces(pieces, tail_chars_of(pieces, self._source_of))

    def splice(self, start: int, end: int, content: Content) -> Content:
        """Replace the bytes `[start, end)` by `content` and return the removed bytes as `Content`.

        Contiguous pieces of the same source are merged afterwards.
        Nothing changes when a boundary is rejected.

        Raises:
            ValueError: when the range is invalid or a boundary is between a CR and an LF.
        """
        self._check_range(start, end)
        self.check_boundary(start)
        self.check_boundary(end)
        first = self.split_at(start)
        last = self.split_at(end)
        removed = list(self._walk_count(first, last - first))
        for _ in range(last - first):
            self._delete_at(self._root, first)
        self._collapse_root()
        pos = first
        for piece in content.pieces:
            previous = self.piece_at(pos - 1) if pos > 0 else None
            joined = merge_pieces(previous, piece) if previous is not None else None
            if joined is not None:
                self._replace_at(self._root, pos - 1, _to_row(joined))
            else:
                self._insert_piece(pos, piece)
                pos += 1
        self._try_merge(pos)
        return Content.from_pieces(removed, tail_chars_of(removed, self._source_of))

    def _check_range(self, start: int, end: int) -> None:
        if not 0 <= start <= end <= self.length:
            raise ValueError(f"range [{start}, {end}) outside 0..{self.length}")

    def _walk_count(self, index: int, count: int) -> Iterator[Piece]:
        for n, piece in enumerate(self._walk(self._root, index)):
            if n >= count:
                break
            yield piece

    def _try_merge(self, index: int) -> None:
        """Merge the pieces `index - 1` and `index` when they are contiguous in one source."""
        if not 0 < index < self.piece_count:
            return
        joined = merge_pieces(self.piece_at(index - 1), self.piece_at(index))
        if joined is None:
            return
        self._replace_at(self._root, index - 1, _to_row(joined))
        self._delete_at(self._root, index)
        self._collapse_root()

    def _collapse_root(self) -> None:
        while isinstance(self._root, _Inner) and len(self._root.children) == 1:
            self._root = self._root.children[0]

    # ----- node primitives -----------------------------------------------------------------------------------------------------------

    @staticmethod
    def _child_for(node: _Inner, index: int, *, for_insert: bool) -> tuple[int, int]:
        base = 0
        last = len(node.children) - 1
        for i, child in enumerate(node.children):
            if index < base + child.count or (for_insert and index == base + child.count) or i == last:
                return i, index - base
            base += child.count
        raise AssertionError("tree without children")

    def _insert_piece(self, index: int, piece: Piece) -> None:
        sibling = self._insert_into(self._root, index, _to_row(piece))
        if sibling is not None:
            self._root = _Inner([self._root, sibling])

    def _insert_into(self, node: _Node, index: int, row: _Row) -> _Node | None:
        if isinstance(node, _Leaf):
            node.insert_row(index, row)
            sibling = node.split_half() if node.size() > self._fanout else None
            node.refresh()
            if sibling is not None:
                sibling.refresh()
            return sibling
        i, local = self._child_for(node, index, for_insert=True)
        new = self._insert_into(node.children[i], local, row)
        if new is not None:
            node.children.insert(i + 1, new)
        sibling_inner = None
        if len(node.children) > self._fanout:
            mid = len(node.children) // 2
            sibling_inner = _Inner(node.children[mid:])
            del node.children[mid:]
        node.refresh()
        return sibling_inner

    def _replace_at(self, node: _Node, index: int, row: _Row) -> None:
        if isinstance(node, _Leaf):
            node.set_row(index, row)
            node.refresh()
            return
        i, local = self._child_for(node, index, for_insert=False)
        self._replace_at(node.children[i], local, row)
        node.refresh()

    def _delete_at(self, node: _Node, index: int) -> None:
        if isinstance(node, _Leaf):
            node.delete_row(index)
            node.refresh()
            return
        i, local = self._child_for(node, index, for_insert=False)
        child = node.children[i]
        self._delete_at(child, local)
        if child.size() < self._min and len(node.children) > 1:
            self._rebalance(node, i)
        node.refresh()

    def _rebalance(self, parent: _Inner, i: int) -> None:
        li = i - 1 if i > 0 else i
        left, right = parent.children[li], parent.children[li + 1]
        if left.size() + right.size() <= self._fanout:
            _absorb(left, right)
            del parent.children[li + 1]
            return
        while left.size() < right.size() - 1:
            _move(right, left, from_front=True)
        while right.size() < left.size() - 1:
            _move(left, right, from_front=False)

    # ----- invariants ----------------------------------------------------------------------------------------------------------------

    def check_invariants(self, *, deep: bool = False) -> None:
        """Recompute every aggregate and count and check the structure; raise `AssertionError` on a violation.

        With `deep`, every piece is also verified against its source (breaks, flags, row0); this reads the bytes of every piece.
        """
        self._check_node(self._root, is_root=True, deep=deep)
        if isinstance(self._root, _Inner):
            self._fail(len(self._root.children) >= _TWO, "inner root with fewer than two children")

    @staticmethod
    def _fail(ok: bool, message: str) -> None:
        if not ok:
            raise AssertionError(message)

    def _check_node(self, node: _Node, *, is_root: bool, deep: bool) -> int:
        """Return the height of `node` after checking it and its descendants."""
        self._fail(node.size() <= self._fanout, "node over fanout")
        self._fail(is_root or node.size() >= self._min, "node under minimum occupancy")
        agg = EMPTY_AGGREGATE
        count = 0
        if isinstance(node, _Leaf):
            self._fail(len({len(col) for col in node.cols}) == 1, "leaf columns differ in length")
            height = 0
            for i in range(node.size()):
                piece = node.piece(i)
                self._fail(piece.a < piece.b, "empty piece")
                self._fail(piece.breaks <= piece.length, "more breaks than bytes")
                agg = combine(agg, piece.aggregate)
                count += 1
                if deep:
                    self._check_piece(piece)
        else:
            self._fail(bool(node.children), "inner node without children")
            heights = {self._check_node(child, is_root=False, deep=deep) for child in node.children}
            self._fail(len(heights) == 1, "leaves at different depths")
            height = heights.pop() + 1
            for child in node.children:
                agg = combine(agg, child.agg)
                count += child.count
        self._fail(node.agg == agg, f"stale aggregate {node.agg} != {agg}")
        self._fail(node.count == count, f"stale count {node.count} != {count}")
        return height

    def _check_piece(self, piece: Piece) -> None:
        source = self._source_of(piece.src)
        data = source.read(piece.a, piece.b)
        self._fail(len(data) == piece.length, f"piece beyond its source: {piece}")
        self._fail(piece.breaks == len(_BREAK.findall(data)), f"wrong break count: {piece}")
        self._fail(piece.first_is_lf == (data[:1] == b"\n"), f"wrong first_is_lf: {piece}")
        self._fail(piece.last_is_cr == (data[-1:] == b"\r"), f"wrong last_is_cr: {piece}")
        self._fail(piece.row0 == source.row_of(piece.a), f"wrong row0: {piece}")
