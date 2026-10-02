"""`RowScanner` fed in blocks gives the same entries, long rows and count as a `LineIndex` scan."""

from __future__ import annotations

import pytest

from nova_editor.core import BytesSource, LineIndex
from nova_editor.core.row_scanner import RowScanner

STRIDE = 4
THRESHOLD = 64
BLOCKS = [1, 2, 7, 64 * 1024]
LONG_ROW = b"x" * (THRESHOLD * 3)
CASES: dict[str, bytes] = {
    "crlf": b"".join(b"row %d\r\n" % i for i in range(40)),
    "lf": b"".join(b"row %d\n" % i for i in range(40)),
    "lone_cr": b"".join(b"row %d\r" % i for i in range(40)),
    "mixed": b"a\r\nb\nc\rd\r\n\r\n\n\re\r\rf\n\r\ng" * 6,
    "no_trailing_newline": b"one\ntwo\nthree\nfour\nfive\nsix",
    "empty": b"",
    "long_row": b"a\n" + LONG_ROW + b"\nb\n" + LONG_ROW + b"\r\n" + b"c\n" * 9 + LONG_ROW,
    "cr_at_block_edge": b"abcdef\r\nghijkl\r\nm\r\n" * 5,
}


def scan_all(data: bytes, block: int, *, stride: int = STRIDE, threshold: int = THRESHOLD) -> tuple[int, list[int], list[tuple[int, int, int]], int]:
    scanner = RowScanner(stride, threshold)
    entries: list[int] = []
    longs: list[tuple[int, int, int]] = []
    for base in range(0, len(data), block):
        chunk = data[base : base + block]
        scanner.feed(chunk, base, final=base + len(chunk) >= len(data))
        got_entries, got_longs = scanner.take()
        entries += got_entries
        longs += got_longs
    last = scanner.finish(len(data))
    return scanner.count, entries, longs + last, scanner.prev


def reference(data: bytes) -> tuple[int, list[int], list[tuple[int, int, int]]]:
    index = LineIndex(BytesSource(data), stride=STRIDE, long_line_threshold=THRESHOLD)
    index.scan_now()
    snapshot = index.snapshot()
    assert snapshot.complete
    entries = list(index._starts)[1:]
    longs = list(zip(index._long_rows, index._long_starts, index._long_ends, strict=True))
    return snapshot.count, entries, longs


@pytest.mark.parametrize("block", BLOCKS)
@pytest.mark.parametrize("name", list(CASES))
def test_scanner_matches_line_index(name: str, block: int) -> None:
    data = CASES[name]
    count, entries, longs, _prev = scan_all(data, block)
    want_count, want_entries, want_longs = reference(data)
    assert count == want_count
    assert entries == want_entries
    assert longs == want_longs
