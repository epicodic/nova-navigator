"""`LineIndex.from_scan` over a fed `RowScanner` equals a fresh scan of the same bytes."""

from __future__ import annotations

import pytest

from nova_editor.core import BytesSource, LineIndex
from nova_editor.core.row_scanner import LongRow, RowScanner

STRIDE = 4
THRESHOLD = 64
BLOCK = 7
LONG_ROW = b"x" * (THRESHOLD * 3)
CASES: dict[str, bytes] = {
    "crlf": b"".join(b"row %d\r\n" % i for i in range(40)),
    "lf": b"".join(b"row %d\n" % i for i in range(40)),
    "lone_cr": b"".join(b"row %d\r" % i for i in range(40)),
    "mixed": b"a\r\nb\nc\rd\r\n\r\n\n\re\r\rf\n\r\ng" * 6,
    "empty": b"",
    "long_rows": b"a\n" + LONG_ROW + b"\nb\n" + LONG_ROW + b"\r\n" + b"c\n" * 9 + LONG_ROW,
}


def _scanned(data: bytes) -> tuple[RowScanner, list[int], list[LongRow]]:
    scanner = RowScanner(STRIDE, THRESHOLD)
    entries: list[int] = []
    longs: list[LongRow] = []
    for base in range(0, len(data), BLOCK):
        chunk = data[base : base + BLOCK]
        scanner.feed(chunk, base, final=base + len(chunk) >= len(data))
        got_entries, got_longs = scanner.take()
        entries += got_entries
        longs += got_longs
    longs += scanner.finish(len(data))
    return scanner, entries, longs


@pytest.mark.parametrize("name", list(CASES))
def test_from_scan_equals_a_fresh_scan(name: str) -> None:
    data = CASES[name]
    scanner, entries, longs = _scanned(data)
    built = LineIndex.from_scan(BytesSource(data), scanner, entries, longs, len(data), stride=STRIDE, long_line_threshold=THRESHOLD)
    fresh = LineIndex(BytesSource(data), stride=STRIDE, long_line_threshold=THRESHOLD)
    fresh.scan_now()
    assert built.snapshot() == fresh.snapshot()
    assert built.snapshot().complete
    assert built.snapshot().scanned_bytes == len(data)
    assert built.stored_entries() == fresh.stored_entries()
    assert built.long_row_count == fresh.long_row_count
    assert built.long_overflow == fresh.long_overflow
    for row in range(fresh.snapshot().count):
        assert built.row_range(row) == fresh.row_range(row)
    for offset in range(0, len(data) + 1, 5):
        assert built.row_at_offset(offset) == fresh.row_at_offset(offset)


def test_from_scan_refuses_a_second_scan() -> None:
    data = CASES["mixed"]
    scanner, entries, longs = _scanned(data)
    built = LineIndex.from_scan(BytesSource(data), scanner, entries, longs, len(data), stride=STRIDE, long_line_threshold=THRESHOLD)
    with pytest.raises(RuntimeError, match="already started"):
        built.start()
    with pytest.raises(RuntimeError, match="already started"):
        built.scan_now()
