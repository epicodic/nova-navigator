"""Plain-bytes reference model and an in-memory `PieceSource` for the piece tree tests."""

from __future__ import annotations

import re
from bisect import bisect_right

from nova_editor.core.pieces import RowNotIndexed

BREAK = re.compile(rb"\r\n|\n|\r")

# Junction alphabet: terminators, multibyte characters and the pieces of a split UTF-8 sequence.
ALPHABET: list[bytes] = [b"a", b"\r", b"\n", "é".encode(), b"\xff", b"\xe2", b"\x82", b"\xac", b"\t", "漢".encode(), b"\xef\xbb\xbf"]


ORIGINALS: dict[str, bytes] = {
    "lf": b"alpha\nbeta\n\ngamma\ndelta\nepsilon\nzeta\n",
    "crlf": b"alpha\r\nbeta\r\n\r\ngamma\r\ndelta\r\nepsilon\r\n",
    "cr": b"alpha\rbeta\r\rgamma\rdelta\repsilon\r",
    "mixed": b"a\r\nb\nc\rd\r\r\n\n\re\xff\xe2\x82\xac\r\nf\t\n",
    "no-trailing": b"alpha\nbeta\r\ngamma\rlast row without newline",
    "empty": b"",
}


def break_ends(data: bytes) -> list[int]:
    """Offsets just after every terminator (CRLF, LF, lone CR)."""
    return [m.end() for m in BREAK.finditer(data)]


def rows(data: bytes) -> list[bytes]:
    """The rows of `data` split on terminators, terminators removed."""
    return re.split(rb"\r\n|\n|\r", data)


def tail_chars(data: bytes) -> int:
    """Characters after the last terminator byte, decoded with surrogateescape."""
    cut = max(data.rfind(b"\r"), data.rfind(b"\n"))
    return len(data[cut + 1 :].decode("utf-8", "surrogateescape"))


def inside_crlf(data: bytes, offset: int) -> bool:
    """True when `offset` is between a CR and an LF."""
    return 0 < offset < len(data) and data[offset - 1 : offset + 1] == b"\r\n"


class FakeSource:
    """In-memory `PieceSource` over fixed bytes; rows are numbered by the regex split."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.ends = break_ends(data)

    def row_of(self, offset: int) -> int:
        return bisect_right(self.ends, offset)

    def row_start(self, row: int) -> int | None:
        if row == 0:
            return 0
        if row <= len(self.ends):
            return self.ends[row - 1]
        return None

    def breaks_between(self, a: int, b: int) -> int:
        return self.row_of(b) - self.row_of(a)

    def read(self, a: int, b: int) -> bytes:
        return self.data[a:b]


class Sources:
    """Registry of fake sources that serves as the tree's `source_of`."""

    def __init__(self) -> None:
        self.items: list[FakeSource] = []

    def add(self, data: bytes) -> int:
        self.items.append(FakeSource(data))
        return len(self.items) - 1

    def __call__(self, src: int) -> FakeSource:
        return self.items[src]


class Rng:
    """Tiny seeded generator (splitmix64) so the fuzz runs are reproducible without the `random` module."""

    def __init__(self, seed: int) -> None:
        self.state = seed

    def next(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return z ^ (z >> 31)

    def randint(self, low: int, high: int) -> int:
        return low + self.next() % (high - low + 1)

    def choice[T](self, items: list[T]) -> T:
        return items[self.next() % len(items)]


class Frontier(FakeSource):
    """The original as the row oracle sees it mid-scan: nothing at or beyond `frontier` can be asked."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.frontier = 0
        self.row_calls = 0

    def row_of(self, offset: int) -> int:
        self.row_calls += 1
        if offset >= self.frontier:
            raise RowNotIndexed(offset)
        return super().row_of(offset)

    def row_start(self, row: int) -> int | None:
        found = super().row_start(row)
        return None if found is None or found > self.frontier else found

    def breaks_between(self, a: int, b: int) -> int:
        return self.row_of(b) - self.row_of(a)
