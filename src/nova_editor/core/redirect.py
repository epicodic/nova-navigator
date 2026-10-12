"""Redirect of history and clipboard references off overwritten original ranges (ACT3 design 5).

An in-place save overwrites original ranges; the old bytes were copied into the add store first.
`Redirect.apply` rewrites a `Content` so that no piece names an overwritten original range any more: the parts inside a copy become pieces of
the copy, the parts outside stay original.
The bytes and the break facts of the content do not change.
The function is pure (no shared state is mutated), so a history can map all its references through it all-or-nothing.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from nova_editor.core.add_store import AddStore
from nova_editor.core.pieces import Content, Piece, PieceSource, RowNotIndexed, make_piece, make_src, make_unknown_piece, merge_pieces

__all__ = ["Copy", "Redirect"]


@dataclass(frozen=True)
class Copy:
    """The old bytes of the original range `[a, b)`, saved at `seg_a` of add segment `segment`."""

    a: int
    b: int
    segment: int
    seg_a: int


class Redirect:
    """Pure translation of `Content` from original ranges to their copies in the add store.

    Raises:
        ValueError: (constructor) a copy is empty, has a negative location, or two copies overlap.
    """

    def __init__(self, copies: Sequence[Copy], original: PieceSource, store: AddStore) -> None:
        ordered = sorted(copies, key=lambda copy: copy.a)
        for copy in ordered:
            if copy.a < 0 or copy.b <= copy.a or copy.segment < 1 or copy.seg_a < 0:
                raise ValueError(f"invalid copy {copy}")
        for left, right in pairwise(ordered):
            if right.a < left.b:
                raise ValueError(f"overlapping copies {left} and {right}")
        self._copies = ordered
        self._ends = [copy.b for copy in ordered]
        self._original = original
        self._store = store

    def apply(self, content: Content) -> Content:
        """Return `content` with every part inside a copy replaced by a piece of the copy; same bytes and break facts.

        A content that touches no copy is returned as it is.
        """
        if not self._copies:
            return content
        built: list[tuple[Piece, bool]] = []  # (piece, rewritten): untouched pieces are never merged
        changed = False
        for piece in content.pieces:
            parts = self._cut(piece) if piece.src == 0 else None
            if parts is None:
                built.append((piece, False))
                continue
            changed = True
            built.extend((part, True) for part in parts)
        if not changed:
            return content
        merged: list[tuple[Piece, bool]] = []
        for piece, rewritten in built:
            if merged and rewritten and merged[-1][1] and (joined := merge_pieces(merged[-1][0], piece)) is not None:
                merged[-1] = (joined, True)
            else:
                merged.append((piece, rewritten))
        return Content.from_pieces([piece for piece, _ in merged], content.tail_chars)

    def _cut(self, piece: Piece) -> list[Piece] | None:
        """Return the parts of an original `piece` against the copies, or `None` when no copy overlaps it."""
        index = bisect_right(self._ends, piece.a)
        if index >= len(self._copies) or self._copies[index].a >= piece.b:
            return None
        parts: list[Piece] = []
        cursor = piece.a
        while index < len(self._copies) and self._copies[index].a < piece.b:
            copy = self._copies[index]
            low = max(copy.a, cursor)
            if low > cursor:
                parts.append(self._outside(piece, cursor, low))
            high = min(copy.b, piece.b)
            parts.append(self._inside(copy, low, high))
            cursor = high
            index += 1
        if cursor < piece.b:
            parts.append(self._outside(piece, cursor, piece.b))
        return parts

    def _inside(self, copy: Copy, a: int, b: int) -> Piece:
        """Return the piece of the copy's add segment that holds the original bytes `[a, b)`."""
        start = copy.seg_a + a - copy.a
        return make_piece(self._store.segment(copy.segment), make_src(0, copy.segment), start, start + b - a)

    def _outside(self, piece: Piece, a: int, b: int) -> Piece:
        """Return the original sub-piece `[a, b)` of `piece`; it stays unknown when `piece` was or the row oracle cannot answer."""
        if piece.unknown:
            return make_unknown_piece(self._original, 0, a, b)
        try:
            return make_piece(self._original, 0, a, b)
        except RowNotIndexed:
            return make_unknown_piece(self._original, 0, a, b)
