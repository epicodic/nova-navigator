"""`PieceSource` adapter for the original file, built on `LineIndex` and its `ByteSource` (ACT4 design 4.4)."""

from __future__ import annotations

from nova_editor.core.byte_source import ByteSource
from nova_editor.core.line_index import LineIndex


class RowNotIndexed(LookupError):
    """The row oracle cannot answer: the scan has not reached the offset yet, or the read budget was exceeded."""


class OriginalSource:
    """Answers the `PieceSource` protocol for the original source without any new scan."""

    def __init__(self, line_index: LineIndex, source: ByteSource) -> None:
        self._index = line_index
        self._source = source

    def row_of(self, offset: int) -> int:
        """Return the row containing `offset`.

        Raises:
            RowNotIndexed: when the index cannot resolve the offset (yet).
        """
        found = self._index.row_at_offset(offset)
        if found is None:
            raise RowNotIndexed(f"offset {offset} is not indexed")
        return found[0]

    def row_start(self, row: int) -> int | None:
        """Return the start offset of `row`, or `None` when the index does not know it.

        When the index cannot resolve `row` itself (its end is unscanned or over the read budget), the end of the previous row is used.
        """
        found = self._index.row_range(row)
        if found is not None:
            return found.start
        previous = self._index.row_range(row - 1) if 0 < row < self._index.snapshot().count else None
        return None if previous is None else previous.end

    def breaks_between(self, a: int, b: int) -> int:
        return self.row_of(b) - self.row_of(a)

    def read(self, a: int, b: int, *, cache: bool = True) -> bytes:
        """Return the bytes `[a, b)`; `cache=False` marks a bulk scan read (see `ByteSource.read`)."""
        return self._source.read(a, max(b - a, 0), cache=cache)
