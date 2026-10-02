"""Pieces, the break aggregate and `Content` of the piece table (ACT4 design 4.1 to 4.3).

A document is the concatenation of pieces.
A piece names a byte range of an immutable source; the bytes themselves are never copied.
Sources answer the small `PieceSource` protocol, so the tree is testable with an in-memory fake.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import NamedTuple, Protocol

LF = 0x0A
CR = 0x0D

_BREAK = re.compile(rb"\r\n|\n|\r")


class PieceSource(Protocol):
    """Row oracle and byte reader of one source (the original or one add segment).

    Rows are numbered inside the source.
    A terminator (LF, CRLF or lone CR) belongs to the row it ends.
    """

    def row_of(self, offset: int) -> int:
        """Return the row number inside the source of the row that contains byte `offset`."""
        ...

    def row_start(self, row: int) -> int | None:
        """Return the start offset of `row`, or `None` when it is not known (yet)."""
        ...

    def breaks_between(self, a: int, b: int) -> int:
        """Return `row_of(b) - row_of(a)` for boundaries that are not inside a terminator."""
        ...

    def read(self, a: int, b: int) -> bytes:
        """Return the bytes `[a, b)`, shorter when the source ends earlier."""
        ...


SourceOf = Callable[[int], PieceSource]
"""Maps a piece's `src` number to its source."""


class Aggregate(NamedTuple):
    """Summary of a run of bytes: length, number of breaks and the two junction flags."""

    length: int
    breaks: int
    first_is_lf: bool
    last_is_cr: bool


EMPTY_AGGREGATE = Aggregate(0, 0, False, False)


def combine(left: Aggregate, right: Aggregate) -> Aggregate:
    """Combine two aggregates (associative).

    A CR at the end of `left` followed by an LF at the start of `right` is one CRLF, so one break is subtracted.
    """
    if left.length == 0:
        return right
    if right.length == 0:
        return left
    junction = left.last_is_cr and right.first_is_lf
    return Aggregate(left.length + right.length, left.breaks + right.breaks - junction, left.first_is_lf, right.last_is_cr)


def aggregate_of_bytes(data: bytes) -> Aggregate:
    """Return the aggregate of `data` read on its own."""
    if not data:
        return EMPTY_AGGREGATE
    return Aggregate(len(data), len(_BREAK.findall(data)), data[0] == LF, data[-1] == CR)


class Piece(NamedTuple):
    """Byte range `[a, b)` of source `src` (never empty), with the facts the tree needs.

    `breaks` counts the terminators of the bytes read on their own; `row0` is the row, inside the source, that contains byte `a`.
    """

    src: int
    a: int
    b: int
    breaks: int
    row0: int
    first_is_lf: bool
    last_is_cr: bool

    @property
    def length(self) -> int:
        """Number of bytes."""
        return self.b - self.a

    @property
    def aggregate(self) -> Aggregate:
        """The aggregate of this piece alone."""
        return Aggregate(self.b - self.a, self.breaks, self.first_is_lf, self.last_is_cr)


def make_piece(source: PieceSource, src: int, a: int, b: int) -> Piece:
    """Build the piece `[a, b)` of `source`, deriving every field from the source.

    Raises:
        ValueError: when the range is empty.
    """
    if not 0 <= a < b:
        raise ValueError(f"empty or invalid piece range [{a}, {b})")
    breaks = source.breaks_between(a, b)
    tail = source.read(b - 1, b + 1)
    last_is_cr = tail[:1] == b"\r"
    if last_is_cr and tail[1:2] == b"\n":
        # A CR that ends the piece counts on its own even when the source continues with an LF.
        breaks += 1
    return Piece(src, a, b, breaks, source.row_of(a), source.read(a, a + 1) == b"\n", last_is_cr)


def merge_pieces(left: Piece, right: Piece) -> Piece | None:
    """Return the single piece that replaces two adjacent pieces contiguous in the same source, else `None`."""
    if left.src != right.src or left.b != right.a:
        return None
    junction = left.last_is_cr and right.first_is_lf
    return Piece(left.src, left.a, right.b, left.breaks + right.breaks - junction, left.row0, left.first_is_lf, right.last_is_cr)


_TAIL_WINDOW = 4096


def tail_chars_of(pieces: Iterable[Piece], source_of: SourceOf) -> int:
    """Return the number of characters after the last break of the concatenated `pieces`.

    Decoding is over the joined bytes (UTF-8 with `surrogateescape`), so a character that spans pieces counts once.
    The cost is proportional to the length of the last row part.
    """
    later: list[bytes] = []
    for piece in reversed(list(pieces)):
        source = source_of(piece.src)
        stop = piece.b
        while stop > piece.a:
            # Read backwards in windows so that a piece with a break near its end is not read whole.
            begin = max(piece.a, stop - _TAIL_WINDOW)
            chunk = source.read(begin, stop)
            cut = max(chunk.rfind(b"\r"), chunk.rfind(b"\n"))
            if cut >= 0:
                later.append(chunk[cut + 1 :])
                return len(b"".join(reversed(later)).decode("utf-8", "surrogateescape"))
            later.append(chunk)
            stop = begin
    return len(b"".join(reversed(later)).decode("utf-8", "surrogateescape"))


@dataclass(frozen=True, slots=True)
class Content:
    """Immutable run of pieces with its aggregate; what `splice`, undo records and the clipboard exchange.

    `tail_chars` is the number of characters after the last break (the whole content when it has no break).
    """

    pieces: tuple[Piece, ...]
    length: int
    breaks: int
    tail_chars: int
    first_is_lf: bool
    last_is_cr: bool

    @staticmethod
    def from_pieces(pieces: Iterable[Piece], tail_chars: int) -> Content:
        """Build a content; the caller supplies `tail_chars` (see `tail_chars_of`)."""
        run = tuple(pieces)
        aggregate = EMPTY_AGGREGATE
        for piece in run:
            aggregate = combine(aggregate, piece.aggregate)
        return Content(run, aggregate.length, aggregate.breaks, tail_chars, aggregate.first_is_lf, aggregate.last_is_cr)

    @staticmethod
    def of(pieces: Iterable[Piece], source_of: SourceOf) -> Content:
        """Build a content and compute `tail_chars` from the sources."""
        run = tuple(pieces)
        return Content.from_pieces(run, tail_chars_of(run, source_of))

    @property
    def aggregate(self) -> Aggregate:
        """The aggregate of the whole content."""
        return Aggregate(self.length, self.breaks, self.first_is_lf, self.last_is_cr)
