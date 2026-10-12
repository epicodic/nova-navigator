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

GENERATION_SHIFT = 24
SEGMENT_MASK = (1 << GENERATION_SHIFT) - 1
MAX_GENERATION = 255


def make_src(generation: int, segment: int) -> int:
    """Return the `Piece.src` number of `segment` in `generation` (generation 0 is the live original and add store).

    Raises:
        ValueError: `segment` does not fit the low `GENERATION_SHIFT` bits.
    """
    if not 0 <= segment <= SEGMENT_MASK:
        raise ValueError(f"segment {segment} does not fit in {GENERATION_SHIFT} bits")
    return (generation << GENERATION_SHIFT) | segment


def generation_of(src: int) -> int:
    """Return the generation of a `Piece.src` number."""
    return src >> GENERATION_SHIFT


def segment_of(src: int) -> int:
    """Return the segment of a `Piece.src` number (0 is the generation's original, `k >= 1` an add segment)."""
    return src & SEGMENT_MASK


class RowNotIndexed(LookupError):
    """The row oracle cannot answer: the scan has not reached the offset yet, or the read budget was exceeded."""


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
    """Summary of a run of bytes: length, number of breaks and the two junction flags.

    A run can hold unknown pieces, whose break count is not known yet (the line scan has not reached them).
    `breaks` then counts the known pieces only, a lower bound; `lead_breaks` counts the breaks before the first unknown piece
    and equals `breaks` when `unknown == 0`.
    """

    length: int
    breaks: int
    first_is_lf: bool
    last_is_cr: bool
    unknown: int = 0
    lead_breaks: int = 0
    first_unknown: bool = False
    last_unknown: bool = False


EMPTY_AGGREGATE = Aggregate(0, 0, False, False)


def combine(left: Aggregate, right: Aggregate) -> Aggregate:
    """Combine two aggregates (associative).

    A CR at the end of `left` followed by an LF at the start of `right` is one CRLF, so one break is subtracted.
    Nothing is subtracted next to an unknown piece: its break count is not part of `breaks`, so the CR and the LF each count on their own side.
    """
    if left.length == 0:
        return right
    if right.length == 0:
        return left
    junction = left.last_is_cr and right.first_is_lf and not left.last_unknown and not right.first_unknown
    breaks = left.breaks + right.breaks - junction
    lead = left.lead_breaks if left.unknown else left.breaks + right.lead_breaks - junction
    return Aggregate(
        left.length + right.length,
        breaks,
        left.first_is_lf,
        right.last_is_cr,
        left.unknown + right.unknown,
        lead,
        left.first_unknown,
        right.last_unknown,
    )


def aggregate_of_bytes(data: bytes) -> Aggregate:
    """Return the aggregate of `data` read on its own."""
    if not data:
        return EMPTY_AGGREGATE
    count = len(_BREAK.findall(data))
    return Aggregate(len(data), count, data[0] == LF, data[-1] == CR, 0, count)


class Piece(NamedTuple):
    """Byte range `[a, b)` of source `src` (never empty), with the facts the tree needs.

    `breaks` counts the terminators of the bytes read on their own; `row0` is the row, inside the source, that contains byte `a`.
    An unknown piece (`unknown` is true) is an original range beyond the line scan: `breaks` and `row0` are 0 and must not be read.
    """

    src: int
    a: int
    b: int
    breaks: int
    row0: int
    first_is_lf: bool
    last_is_cr: bool
    unknown: bool = False

    @property
    def length(self) -> int:
        """Number of bytes."""
        return self.b - self.a

    @property
    def aggregate(self) -> Aggregate:
        """The aggregate of this piece alone."""
        if self.unknown:
            return Aggregate(self.b - self.a, 0, self.first_is_lf, self.last_is_cr, 1, 0, True, True)
        return Aggregate(self.b - self.a, self.breaks, self.first_is_lf, self.last_is_cr, 0, self.breaks)


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


def make_unknown_piece(source: PieceSource, src: int, a: int, b: int) -> Piece:
    """Build the piece `[a, b)` with honest junction flags (two byte reads) and no break facts.

    Raises:
        ValueError: when the range is empty.
    """
    if not 0 <= a < b:
        raise ValueError(f"empty or invalid piece range [{a}, {b})")
    return Piece(src, a, b, 0, 0, source.read(a, a + 1) == b"\n", source.read(b - 1, b) == b"\r", True)


def merge_pieces(left: Piece, right: Piece) -> Piece | None:
    """Return the single piece that replaces two adjacent pieces contiguous in the same source, else `None`.

    Two known pieces or two unknown pieces merge; a known and an unknown piece never do.
    """
    if left.src != right.src or left.b != right.a or left.unknown != right.unknown:
        return None
    if left.unknown:
        return Piece(left.src, left.a, right.b, 0, 0, left.first_is_lf, right.last_is_cr, True)
    junction = left.last_is_cr and right.first_is_lf
    return Piece(left.src, left.a, right.b, left.breaks + right.breaks - junction, left.row0, left.first_is_lf, right.last_is_cr)


_TAIL_WINDOW = 4096
_TAIL_CAP = 16 * _TAIL_WINDOW


def tail_chars_of(pieces: Iterable[Piece], source_of: SourceOf) -> int:
    """Return the number of characters after the last break of the concatenated `pieces`.

    Decoding is over the joined bytes (UTF-8 with `surrogateescape`), so a character that spans pieces counts once.
    The cost is proportional to the length of the last row part.
    When a piece is unknown its breaks are not known, so at most `_TAIL_CAP` bytes are read and the result is a lower bound.
    """
    run = list(pieces)
    capped = any(piece.unknown for piece in run)
    read = 0
    later: list[bytes] = []
    for piece in reversed(run):
        source = source_of(piece.src)
        stop = piece.b
        while stop > piece.a:
            if capped and read >= _TAIL_CAP:
                return len(b"".join(reversed(later)).decode("utf-8", "surrogateescape"))
            # Read backwards in windows so that a piece with a break near its end is not read whole.
            begin = max(piece.a, stop - _TAIL_WINDOW)
            chunk = source.read(begin, stop)
            read += len(chunk)
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
    `breaks` is a lower bound when `unknown > 0`; `lead_breaks` counts the breaks before the first unknown piece.
    """

    pieces: tuple[Piece, ...]
    length: int
    breaks: int
    tail_chars: int
    first_is_lf: bool
    last_is_cr: bool
    unknown: int = 0
    lead_breaks: int = 0

    @staticmethod
    def from_pieces(pieces: Iterable[Piece], tail_chars: int) -> Content:
        """Build a content; the caller supplies `tail_chars` (see `tail_chars_of`)."""
        run = tuple(pieces)
        aggregate = EMPTY_AGGREGATE
        for piece in run:
            aggregate = combine(aggregate, piece.aggregate)
        return Content(
            run,
            aggregate.length,
            aggregate.breaks,
            tail_chars,
            aggregate.first_is_lf,
            aggregate.last_is_cr,
            aggregate.unknown,
            aggregate.lead_breaks,
        )

    @staticmethod
    def of(pieces: Iterable[Piece], source_of: SourceOf) -> Content:
        """Build a content and compute `tail_chars` from the sources."""
        run = tuple(pieces)
        return Content.from_pieces(run, tail_chars_of(run, source_of))

    @property
    def aggregate(self) -> Aggregate:
        """The aggregate of the whole content."""
        if not self.pieces:
            return EMPTY_AGGREGATE
        return Aggregate(
            self.length,
            self.breaks,
            self.first_is_lf,
            self.last_is_cr,
            self.unknown,
            self.lead_breaks,
            self.pieces[0].unknown,
            self.pieces[-1].unknown,
        )
