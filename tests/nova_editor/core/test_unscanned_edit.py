"""Byte edits on huge unscanned files: no full pass, memory independent of the size, edits while the scan runs (ACT1 acceptance, REQ-12, REQ-21)."""

from __future__ import annotations

import os
import threading
import tracemalloc
from pathlib import Path

import pytest

from nova_editor.core import AddStore, Content, LineIndex, PieceTable, PreadSource
from nova_editor.document._lazy_document import LazyDocument

from .helpers import CountingSource, reference_rows
from .reference import Rng

CR = 0x0D
LF = 0x0A
GIB = 1 << 30
MARKER = b"END\n"
EMPTY = Content.from_pieces((), 0)
JOIN_SECONDS = 60.0
NO_FULL_PASS_BYTES = 64 * 1024  # one block: a full pass over a gibibyte would read a million times this
MEMORY_TOLERANCE = 64 * 1024
SCAN_THREAD = "line-index-scan"
INSERTS: list[bytes] = [b"x", b"ab\t", b"\n", b"\r", b"\r\n"]


def sparse_file(path: Path, size: int) -> Path:
    """Create a `size`-byte file whose end holds `MARKER`; skip the test when the file system does not keep it sparse."""
    with path.open("wb") as handle:
        handle.truncate(size)
        allocated = os.fstat(handle.fileno()).st_blocks * 512
        if allocated >= size // 2:
            pytest.skip("filesystem has no sparse files")
        handle.seek(size - len(MARKER))
        handle.write(MARKER)
    return path


def open_table(path: Path) -> tuple[PieceTable, CountingSource, LineIndex]:
    """Open `path` as a piece table whose line index is never started: the scan frontier stays at 0."""
    source = CountingSource(PreadSource(path))
    index = LineIndex(source, stride=1024)
    table = PieceTable(source, index, AddStore())
    return table, source, index


def close_all(source: CountingSource, index: LineIndex) -> None:
    index.cancel()
    source.close()


@pytest.mark.parametrize("size", [GIB, 50 * GIB])
def test_edits_at_the_end_need_no_full_pass(tmp_path: Path, size: int) -> None:
    table, counting, index = open_table(sparse_file(tmp_path / "big.bin", size))
    try:
        assert table.length == size
        table.splice(size, size, table.add(b"tail"))  # insert at the very end: "...END\ntail"
        table.splice(size - 4, size, EMPTY)  # delete "END\n": zeros, then "tail" at size - 4
        table.splice(size - 5, size - 4, table.add(b"X"))  # overwrite the zero byte before "tail"
        assert table.length == size
        assert table.read(size - 5, 5) == b"Xtail"
        assert table.read(size - 6, 1) == b"\x00"
        assert counting.bytes_read <= NO_FULL_PASS_BYTES
        assert table.row_range(1) is None  # beyond the frontier: unresolved, no exception
        assert table.snapshot().complete is False
        table.check_invariants()
    finally:
        close_all(counting, index)


def test_memory_does_not_grow_with_the_file_size(tmp_path: Path) -> None:
    peaks: list[tuple[int, int]] = []
    for size in (GIB, 50 * GIB):
        table, counting, index = open_table(sparse_file(tmp_path / f"m{size}.bin", size))
        try:
            tracemalloc.start()
            try:
                end = table.length
                for k in range(3):
                    table.splice(end - 1 - k, end - 1 - k, table.add(b"x"))
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            peaks.append((peak, table.tree.piece_count))
        finally:
            close_all(counting, index)
    assert abs(peaks[0][0] - peaks[1][0]) < MEMORY_TOLERANCE
    assert peaks[0][1] == peaks[1][1]


@pytest.mark.parametrize("size", [GIB, 50 * GIB])
def test_lazy_document_splices_bytes_on_a_sparse_file(tmp_path: Path, size: int) -> None:
    doc = LazyDocument.from_path(sparse_file(tmp_path / "doc.bin", size))
    try:
        assert doc.length == size
        doc.splice_bytes(size - len(MARKER), size, EMPTY)  # delete the marker at the end
        assert doc.length == size - len(MARKER)
        assert doc.read_bytes(size - 8, 8) == bytes(4)  # the four zero bytes before the marker
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)


def _between_crlf(ref: bytearray, offset: int) -> bool:
    """True when `offset` lies between the CR and the LF of `ref` (the table refuses such a boundary)."""
    return 0 < offset < len(ref) and ref[offset - 1] == CR and ref[offset] == LF


def _random_edit(rng: Rng, ref: bytearray) -> tuple[int, int, bytes] | None:
    """Pick one edit `(start, end, data)` that the table accepts; `None` when the random pick would split a CRLF."""
    size = len(ref)
    start = rng.randint(0, size)
    if rng.randint(0, 1) == 0:
        end, data = start, rng.choice(INSERTS)
    else:
        end, data = min(size, start + rng.randint(1, 20)), b""
    if _between_crlf(ref, start) or _between_crlf(ref, end):
        return None
    if data.startswith(b"\n") and start > 0 and ref[start - 1] == CR:
        return None
    if data.endswith(b"\r") and end < size and ref[end] == LF:
        return None
    return start, end, data


def _apply(table: PieceTable, ref: bytearray, start: int, end: int, data: bytes) -> None:
    table.splice(start, end, table.add(data) if data else EMPTY)
    ref[start:end] = data


def _assert_bytes_equal(table: PieceTable, ref: bytearray, step: int = 1 << 20) -> None:
    assert table.length == len(ref)
    for offset in range(0, len(ref), step):
        assert table.read(offset, step) == bytes(ref[offset : offset + step]), f"bytes differ at {offset}"


def _open_scanning(path: Path, stride: int) -> tuple[PieceTable, CountingSource, LineIndex]:
    source = CountingSource(PreadSource(path))
    index = LineIndex(source, stride=stride, scan_block=4096)
    table = PieceTable(source, index, AddStore())
    index.start()
    return table, source, index


def _scan_threads(before: set[threading.Thread]) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t not in before and t.name == SCAN_THREAD]


def _bytes_while_scanning(path: Path) -> None:
    """Apply random edits to the 20 MiB file while its scan runs; compare the bytes every 50 edits and at the end."""
    ref = bytearray(path.read_bytes())
    table, source, index = _open_scanning(path, stride=64)
    try:
        rng = Rng(1)
        applied = 0
        while applied < 200:
            edit = _random_edit(rng, ref)
            if edit is None:
                continue
            _apply(table, ref, *edit)
            applied += 1
            if applied % 50 == 0:
                _assert_bytes_equal(table, ref)
        _assert_bytes_equal(table, ref)
        assert table.snapshot().error is None
    finally:
        index.cancel()
        index.join(JOIN_SECONDS)
        source.close()


def _rows_after_scan(path: Path) -> None:
    """Edit the small file, let its scan finish, then every sampled row and the row count must match the reference."""
    ref = bytearray(path.read_bytes())
    table, source, index = _open_scanning(path, stride=64)
    try:
        rng = Rng(2)
        for _ in range(200):
            edit = _random_edit(rng, ref)
            if edit is not None:
                _apply(table, ref, *edit)
        assert index.join(JOIN_SECONDS)
        for _ in range(100_000):
            snap = table.snapshot()
            if snap.complete:
                break
        else:
            pytest.fail("the unknown pieces were never all resolved")
        expected = reference_rows(bytes(ref))
        assert snap.count == len(expected)
        for row in range(0, len(expected), 97):
            found = table.row_range(row)
            assert found is not None, f"row {row} unresolved"
            assert (found.start, found.content_end, found.end) == expected[row], f"row {row}"
        _assert_bytes_equal(table, ref)
    finally:
        index.cancel()
        index.join(JOIN_SECONDS)
        source.close()


def test_edit_while_the_scan_runs(tmp_path: Path) -> None:
    before = set(threading.enumerate())
    big = tmp_path / "big.txt"
    big.write_bytes(b"".join(b"line %d\n" % n for n in range(3_000_000))[: 20 << 20])
    small = tmp_path / "small.txt"
    small.write_bytes(b"".join(b"line %d\n" % n for n in range(200 * 1024 // 9)))
    _bytes_while_scanning(big)
    _rows_after_scan(small)
    leaked = _scan_threads(before)
    for thread in leaked:
        thread.join(JOIN_SECONDS)
    assert [t for t in leaked if t.is_alive()] == []
