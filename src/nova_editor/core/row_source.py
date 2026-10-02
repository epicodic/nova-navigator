"""`ByteSource` over the pieces of one row, with offsets relative to the row (ACT4 design 6.1).

A long-line index reads a `RowSource` and never sees the piece table.
The original file and sealed add segments are immutable, and the tail segment of the add store only grows past the bytes a piece names,
so a scan thread can read a `RowSource` while the UI thread keeps editing the table.
The row is a snapshot: later edits of the table do not change it.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable

from nova_editor.core.byte_source import SourceChanged
from nova_editor.core.original_source import OriginalSource
from nova_editor.core.pieces import Piece, PieceSource, SourceOf


class RowSource:
    """Immutable concatenation of byte ranges of piece sources; implements `ByteSource` and is safe to read from several threads."""

    def __init__(self, pieces: Iterable[Piece], source_of: SourceOf) -> None:
        """Snapshot `pieces`; only `src`, `a` and `b` of each piece are used.

        `source_of` is called here, on the calling thread, for every piece, so the source map of the table is never touched by a reader thread.
        Empty pieces are skipped.
        """
        parts: list[tuple[PieceSource, int, int]] = []
        offsets = [0]
        for piece in pieces:
            if piece.b > piece.a:
                parts.append((source_of(piece.src), piece.a, piece.b))
                offsets.append(offsets[-1] + piece.b - piece.a)
        self._parts = tuple(parts)
        self._offsets = tuple(offsets)

    def length(self) -> int:
        """Return the number of bytes of the row."""
        return self._offsets[-1]

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        """Return `min(size, length - offset)` bytes at the row-relative `offset`, reading across pieces.

        `cache=False` is passed on to the original file (a scan must not evict the blocks the UI uses); the add store has no cache.
        A piece source that returns fewer bytes than asked ends the read early; one that returns none raises `SourceChanged` (it lost bytes the piece names).
        """
        if offset < 0 or size < 0:
            raise ValueError("offset and size must not be negative")
        end = min(offset + size, self._offsets[-1])
        if end <= offset:
            return b""
        index = bisect_right(self._offsets, offset) - 1
        chunks: list[bytes] = []
        position = offset
        while position < end:
            source, a, b = self._parts[index]
            base = self._offsets[index]
            take = min(end, base + (b - a)) - position
            lo = a + position - base
            if not cache and isinstance(source, OriginalSource):
                chunk = source.read(lo, lo + take, cache=False)
            else:
                chunk = source.read(lo, lo + take)
            if not chunk:
                raise SourceChanged(f"short read of a row piece: wanted {take}, got 0")
            chunks.append(chunk)
            position += len(chunk)
            if len(chunk) < take:
                break  # a short read is returned as it is, like a `ByteSource` does; the caller reads on from there
            index += 1
        return chunks[0] if len(chunks) == 1 else b"".join(chunks)

    def close(self) -> None:
        """Nothing to release: the piece sources belong to the table."""
