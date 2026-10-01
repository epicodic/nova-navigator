"""Plain-bytes reference model and an in-memory `PieceSource` for the piece tree tests."""

from __future__ import annotations

import re
from bisect import bisect_right

BREAK = re.compile(rb"\r\n|\n|\r")

# Junction alphabet: terminators, multibyte characters and the pieces of a split UTF-8 sequence.
ALPHABET: list[bytes] = [b"a", b"\r", b"\n", "é".encode(), b"\xff", b"\xe2", b"\x82", b"\xac", b"\t", "漢".encode()]


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
