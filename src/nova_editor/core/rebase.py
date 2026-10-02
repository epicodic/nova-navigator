"""Translation of piece references from the old sources to the saved file (ACT5 design 8.3 and 8.4).

After a save the document is one piece of the new original.
Undo records and the clipboard still hold pieces of the old original and the old add store; `Rebaser` rewrites them.
A part of a piece that the saved file contains (`SaveLayout.intervals`) becomes a piece of the new original, so no text is copied.
A part that the file does not contain (an orphan: text deleted or replaced before the save) is copied into the new add store when the total is small,
otherwise the old original and add store are kept as a legacy generation and the orphan pieces keep their ranges under that generation.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple

from nova_editor.core.add_store import AddStore
from nova_editor.core.byte_source import ByteSource, SourceChanged
from nova_editor.core.line_index import LineIndex
from nova_editor.core.original_source import OriginalSource
from nova_editor.core.pieces import MAX_GENERATION, Content, Piece, PieceSource, generation_of, make_piece, make_src, merge_pieces, segment_of
from nova_editor.core.save_layout import SaveLayout

__all__ = ["UNDO_COPY_LIMIT", "RebasePlan", "Rebaser"]

UNDO_COPY_LIMIT = 8 * 1024 * 1024
"""Orphan bytes up to this total are copied into the new add store; beyond it the old files are kept as a legacy generation."""

_RECOVERABLE = (OSError, LookupError, ValueError, SourceChanged)
"""Failures of the translation that fall back to `RebasePlan.retain_all` (a read of an old source, a row oracle that cannot answer)."""

Legacy = tuple[OriginalSource, AddStore]


@dataclass
class RebasePlan:
    """The outcome of a translation.

    `contents` are the translated contents in the order of the input (empty when `clear_history` is set).
    `new_add_store` holds the copied orphans (empty otherwise).
    `retained` is the old original and add store when they become the next legacy generation of the table.
    `clear_history` means that no generation is free: the caller must drop the history and the clipboard.
    `orphan_bytes` is the total size of the distinct orphan ranges (0 after `retain_all`, which does not measure).
    `source` and `line_index` are the saved file and its row index; the document layer fills them in so that `apply_rebase` needs only the plan.
    """

    contents: list[Content]
    new_add_store: AddStore
    retained: Legacy | None
    clear_history: bool
    orphan_bytes: int
    source: ByteSource | None = None
    line_index: LineIndex | None = None

    @staticmethod
    def retain_all(
        contents: Sequence[Content],
        new_add_store: AddStore,
        legacy: Legacy | None,
        generation: int,
        max_generation: int = MAX_GENERATION,
    ) -> RebasePlan:
        """Keep every old reference as it is under legacy generation `generation` (always correct, only wasteful).

        Raises:
            ValueError: when `legacy` is missing although a generation is free.
        """
        if generation > max_generation:
            return RebasePlan([], new_add_store, None, True, 0)
        if legacy is None:
            msg = "the old files are needed to retain them"
            raise ValueError(msg)
        moved = [_with_pieces(content, [_in_generation(piece, generation) if generation_of(piece.src) == 0 else piece for piece in content.pieces]) for content in contents]
        return RebasePlan(moved, new_add_store, legacy, False, 0)


class _Part(NamedTuple):
    """A part of an old piece: present in the saved file at `out` (`present`), or an orphan range of the old source."""

    present: bool
    a: int
    b: int
    out: int


class Rebaser:
    """Rewrite `Content` objects from the old sources to the saved file."""

    def __init__(
        self,
        layout: SaveLayout,
        old_sources: Sequence[PieceSource],
        new_original: PieceSource,
        new_add_store: AddStore,
        *,
        limit: int = UNDO_COPY_LIMIT,
        max_generation: int = MAX_GENERATION,
        legacy: Legacy | None = None,
        generations: int = 0,
    ) -> None:
        """Create the rebaser.

        Args:
            layout: The runs the save wrote.
            old_sources: The generation 0 sources of the old table by `src` number (the original first, then the add segments).
            new_original: The saved file as a piece source.
            new_add_store: The fresh store that receives copied orphans.
            limit: The largest orphan total that is copied.
            max_generation: The highest legacy generation number (lowered by tests).
            legacy: The old original and add store, kept when the orphans are not copied (required then).
            generations: The number of legacy generations the table has already.
        """
        self._layout = layout
        self._old = old_sources
        self._new_original = new_original
        self._store = new_add_store
        self._limit = limit
        self._max_generation = max_generation
        self._legacy = legacy
        self._generation = generations + 1
        self._intervals: dict[int, tuple[list[int], list[int], list[int]]] = {}

    def translate(self, contents: Sequence[Content]) -> RebasePlan:
        """Return the translated contents, in the order of `contents`.

        Pieces of generations above 0 are untouched; the aggregates of every content are copied, not recomputed.
        Any failure inside falls back to `RebasePlan.retain_all`.
        """
        try:
            return self._translate(contents)
        except _RECOVERABLE:
            return RebasePlan.retain_all(contents, self._store, self._legacy, self._generation, self._max_generation)

    def _translate(self, contents: Sequence[Content]) -> RebasePlan:
        parts: dict[Piece, list[_Part]] = {}
        orphans: dict[tuple[int, int, int], None] = {}
        for content in contents:
            for piece in content.pieces:
                if generation_of(piece.src) != 0 or piece in parts:
                    continue
                found = self._split(piece)
                parts[piece] = found
                for part in found:
                    if not part.present:
                        orphans[piece.src, part.a, part.b] = None
        total = sum(b - a for _, a, b in orphans)
        keep_old = total > self._limit
        if keep_old and self._generation > self._max_generation:
            return RebasePlan([], self._store, None, True, total)
        if keep_old and self._legacy is None:
            msg = "the old files are needed to retain them"
            raise ValueError(msg)
        copied: dict[tuple[int, int, int], Piece] = {}
        if not keep_old:
            for src, a, b in orphans:
                data = self._old[src].read(a, b)
                if len(data) != b - a:
                    msg = f"short read of the old source {src}"
                    raise SourceChanged(msg)
                segment, start, stop = self._store.append(data)
                copied[src, a, b] = make_piece(self._store.segment(segment), segment, start, stop)
        translated = [self._rewrite(content, parts, copied) for content in contents]
        return RebasePlan(translated, self._store, self._legacy if keep_old else None, False, total)

    def _rewrite(self, content: Content, parts: dict[Piece, list[_Part]], copied: dict[tuple[int, int, int], Piece]) -> Content:
        built: list[tuple[Piece, bool]] = []  # (piece, rewritten): legacy pieces stay as they are
        for piece in content.pieces:
            if generation_of(piece.src) != 0:
                built.append((piece, False))
                continue
            for part in parts[piece]:
                if part.present:
                    built.append((make_piece(self._new_original, 0, part.out, part.out + part.b - part.a), True))
                elif (found := copied.get((piece.src, part.a, part.b))) is not None:
                    built.append((found, True))
                else:
                    built.append((self._kept(piece, part), True))
        merged: list[tuple[Piece, bool]] = []
        for piece, rewritten in built:
            if merged and rewritten and merged[-1][1] and (joined := merge_pieces(merged[-1][0], piece)) is not None:
                merged[-1] = (joined, True)
            else:
                merged.append((piece, rewritten))
        return _with_pieces(content, [piece for piece, _ in merged])

    def _kept(self, piece: Piece, part: _Part) -> Piece:
        """Return the orphan part as a piece of the retained generation (the old range, a new `src`)."""
        src = make_src(self._generation, segment_of(piece.src))
        if part.a == piece.a and part.b == piece.b:
            return piece._replace(src=src)
        return make_piece(self._old[piece.src], src, part.a, part.b)

    def _split(self, piece: Piece) -> list[_Part]:
        """Split `piece` against the intervals of its source into present and orphan parts, in order."""
        starts, ends, outs = self._table(piece.src)
        index = bisect_right(ends, piece.a)
        cursor = piece.a
        found: list[_Part] = []
        while index < len(starts) and starts[index] < piece.b:
            low = max(starts[index], cursor)
            if low > cursor:
                found.append(_Part(False, cursor, low, 0))
            high = min(ends[index], piece.b)
            found.append(_Part(True, low, high, outs[index] + low - starts[index]))
            cursor = high
            index += 1
        if cursor < piece.b:
            found.append(_Part(False, cursor, piece.b, 0))
        return found

    def _table(self, src: int) -> tuple[list[int], list[int], list[int]]:
        found = self._intervals.get(src)
        if found is None:
            runs = self._layout.intervals(src)
            found = ([a for a, _, _ in runs], [b for _, b, _ in runs], [out for _, _, out in runs])
            self._intervals[src] = found
        return found


def _in_generation(piece: Piece, generation: int) -> Piece:
    return piece._replace(src=make_src(generation, segment_of(piece.src)))


def _with_pieces(content: Content, pieces: list[Piece]) -> Content:
    """Return `content` with other pieces for the same bytes: the aggregate fields are copied."""
    return replace(content, pieces=tuple(pieces))
