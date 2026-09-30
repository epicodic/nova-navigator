"""Tests for LazyDocument over the core indexes."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from rich.cells import cell_len

from nova_editor.core import PreadSource
from nova_editor.core.line_index import DEFAULT_SCAN_BLOCK
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import MAX_WINDOW_CHARS, LazyDocument, RowUnavailable, WholeLineAccess
from tests.nova_editor.helpers_view import LOWERED_OPTIONS, make_mixed, oracle_row_ranges, oracle_row_text

TERMINATORS = (b"\n", b"\r\n", b"\r")
TAB = 4


def _config(*, yield_seconds: float = 0.0, max_long_indexes: int = 8) -> LazyConfig:
    return LazyConfig(**LOWERED_OPTIONS, yield_seconds=yield_seconds, max_long_indexes=max_long_indexes)


def _open(path: Path, *, max_long_indexes: int = 8) -> LazyDocument:
    doc = LazyDocument.from_path(path, _config(max_long_indexes=max_long_indexes))
    assert doc.wait_indexed(10.0)
    return doc


def _long_row(doc: LazyDocument) -> int:
    """Return the first long row without touching its index."""
    return next(row for row in range(doc.line_count) if doc.is_long(row))


def _settled_long_row(doc: LazyDocument) -> int:
    """Return the first long row once its scan completed."""
    row = _long_row(doc)
    assert doc.long_index(row).join(10.0)
    return row


def _disp(text: str, column: int) -> int:
    """Independent oracle: display column after the first `column` characters."""
    disp = 0
    for char in text[:column]:
        disp += TAB - disp % TAB if char == "\t" else cell_len(char)
    return disp


def _cover(text: str, x: int) -> int:
    """Independent oracle: index of the first character whose end display column exceeds `x`, else the length."""
    disp = 0
    for index, char in enumerate(text):
        disp += TAB - disp % TAB if char == "\t" else cell_len(char)
        if disp > x:
            return index
    return len(text)


class CountingSource:
    """Wraps a PreadSource; records the sizes of reads made by the thread that created it and can slow scan reads down."""

    def __init__(self, path: Path) -> None:
        self._inner = PreadSource(path)
        self._owner = threading.get_ident()
        self.owner_reads: list[int] = []
        self.scan_delay = 0.0

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if threading.get_ident() == self._owner:
            self.owner_reads.append(size)
        elif not cache and self.scan_delay:
            time.sleep(self.scan_delay)
        return self._inner.read(offset, size, cache=cache)

    def close(self) -> None:
        self._inner.close()


def test_rows_match_oracle(tmp_path: Path) -> None:
    for terminator in TERMINATORS:
        path = make_mixed(tmp_path / f"m{terminator!r}.txt", terminator=terminator)
        doc = _open(path)
        data = path.read_bytes()
        assert doc.line_count == len(oracle_row_ranges(data))
        checked = 0
        for row in range(doc.line_count):
            if doc.is_long(row):
                continue
            assert doc.get_line(row) == oracle_row_text(data, row)
            assert doc[row] == doc.get_line(row)
            checked += 1
        assert checked == doc.line_count - 1
        assert doc.newline == terminator.decode()
        doc.close()


def test_row_classes(tmp_path: Path) -> None:
    doc = _open(make_mixed(tmp_path / "m.txt", long_chars=5000))
    classes = [doc.row_class(row) for row in range(doc.line_count)]
    assert classes.count("long") == 1
    assert classes.count("medium") == 1
    assert classes[0] == "short"
    assert doc.row_byte_length(0) == len(b"short line 0")
    doc.close()


def test_row_beyond_count_raises_index_error(tmp_path: Path) -> None:
    doc = _open(make_mixed(tmp_path / "m.txt"))
    with pytest.raises(IndexError):
        doc.get_line(doc.line_count)
    doc.close()


def test_row_that_is_not_resolved_raises_a_clear_error(tmp_path: Path) -> None:
    doc = LazyDocument.from_path(make_mixed(tmp_path / "m.txt"), _config(), autostart=False)
    with pytest.raises(RowUnavailable):
        doc.get_line(0)
    doc.close()


def test_long_row_never_decoded_whole(tmp_path: Path) -> None:
    doc = _open(make_mixed(tmp_path / "m.txt", long_chars=5000))
    long_row = _settled_long_row(doc)
    with pytest.raises(WholeLineAccess):
        doc.get_line(long_row)
    with pytest.raises(WholeLineAccess):
        _ = doc[long_row]
    with pytest.raises(WholeLineAccess):
        _ = doc.text
    with pytest.raises(WholeLineAccess):
        _ = doc.lines
    assert len(doc.column_slice(long_row, 100, 140)) == 40
    assert {method for method, _ in doc.call_log.refusals} >= {"get_line", "text", "lines"}
    doc.close()


def test_read_all_respects_the_limit(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt")
    doc = _open(path)
    size = path.stat().st_size
    assert doc.read_all(size) == path.read_bytes().decode("utf-8", "surrogateescape")
    with pytest.raises(WholeLineAccess):
        doc.read_all(size - 1)
    doc.close()


def test_column_slice_window_never_starts_mid_character(tmp_path: Path) -> None:
    doc = _open(make_mixed(tmp_path / "m.txt", long_chars=5000))
    long_row = _settled_long_row(doc)
    for start in range(0, 400, 7):
        text = doc.column_slice(long_row, start, start + 20)
        assert "�" not in text
        assert len(text) == 20
    doc.close()


def test_column_slice_matches_oracle_and_is_bounded(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=40_000)
    doc = _open(path)
    long_row = _settled_long_row(doc)
    expected = oracle_row_text(path.read_bytes(), long_row)
    assert doc.column_slice(long_row, 5, 900) == expected[5:900]
    big = doc.column_slice(long_row, 0, 10**9)
    assert len(big) <= MAX_WINDOW_CHARS
    assert big == expected[: len(big)]
    assert doc.column_slice(long_row, len(expected) + 10, len(expected) + 20) == ""
    doc.close()


def test_line_count_is_lower_bound_until_complete(tmp_path: Path) -> None:
    doc = LazyDocument.from_path(make_mixed(tmp_path / "m.txt"), _config(), autostart=False)
    assert doc.line_count == 1
    assert doc.snapshot().complete is False
    doc.start_scan()
    doc.start_scan()
    assert doc.start == (0, 0)
    assert doc.wait_indexed(10.0)
    assert doc.snapshot().complete is True
    assert doc.line_count > 1
    doc.close()


def test_replace_range_is_read_only(tmp_path: Path) -> None:
    doc = _open(make_mixed(tmp_path / "m.txt"))
    with pytest.raises(NotImplementedError, match="ACT4"):
        doc.replace_range((0, 0), (0, 0), "x")
    doc.close()


def test_get_text_range_matches_oracle_and_refuses_long_rows(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=3000)
    doc = _open(path)
    data = path.read_bytes()
    assert doc.get_text_range((1, 2), (1, 5)) == oracle_row_text(data, 1)[2:5]
    assert doc.get_text_range((1, 2), (3, 4)) == "\n".join([oracle_row_text(data, 1)[2:], oracle_row_text(data, 2), oracle_row_text(data, 3)[:4]])
    long_row = _settled_long_row(doc)
    assert doc.get_text_range((long_row, 3), (long_row, 30)) == oracle_row_text(data, long_row)[3:30]
    with pytest.raises(WholeLineAccess):
        doc.get_text_range((long_row - 1, 0), (long_row + 1, 0))
    doc.close()


def test_calllog_records_no_whole_decode_on_scroll_like_access(tmp_path: Path) -> None:
    doc = _open(make_mixed(tmp_path / "m.txt", long_chars=20_000))
    for row in range(doc.line_count):
        if doc.is_long(row):
            doc.column_slice(row, 0, 80)
            doc.display_column(row, 50)
            doc.has_char_at(row, 10)
            doc.column_at_display(row, 60)
            doc.byte_offset(row, 70)
            doc.line_length(row)
        else:
            doc.get_line(row)
    assert 0 < doc.call_log.max_decoded_chars <= MAX_WINDOW_CHARS
    assert {"column_slice", "display_column", "has_char_at", "column_at_display", "byte_offset"} <= {event.method for event in doc.call_log.events}
    doc.close()


def test_capabilities_on_long_row_match_oracle(tmp_path: Path) -> None:
    path = tmp_path / "special.txt"
    unit = "ab\tc日é\U0001f600x\t\t"
    row = unit.encode() * 40
    row = row[:300] + b"\xff" + row[300:] + unit.encode() * 40
    path.write_bytes(b"first\n" + row + b"\nlast")
    doc = _open(path)
    row_no = 1
    assert doc.is_long(row_no)
    assert doc.long_index(row_no).join(10.0)
    text = oracle_row_text(path.read_bytes(), row_no)
    assert "\udcff" in text
    start = path.read_bytes().index(b"\n") + 1
    assert doc.line_length(row_no) == len(text)
    assert doc.row_byte_length(row_no) == len(row)
    for column in [*range(0, len(text) + 3, 7), 0, 1, len(text) - 1, len(text), len(text) + 5]:
        expected_disp = _disp(text, column)
        assert doc.display_column(row_no, column) == expected_disp
        assert doc.byte_offset(row_no, column) == start + len(text[:column].encode("utf-8", "surrogateescape"))
        assert doc.has_char_at(row_no, column) is (column < len(text))
    for x in range(0, _disp(text, len(text)) + 4, 5):
        assert doc.column_at_display(row_no, x) == _cover(text, x)
    doc.close()


def test_long_row_capabilities_are_none_beyond_the_frontier(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=100_000)
    source = CountingSource(path)
    doc = LazyDocument(source, _config())
    assert doc.wait_indexed(10.0)
    source.scan_delay = 0.3  # the long scan is stuck in its first read, so only the row start is known
    row = _long_row(doc)
    assert doc.line_length(row) is None
    index = doc.long_index(row)
    assert index.frontier().chars == 0
    assert doc.display_column(row, 5) is None
    assert doc.byte_offset(row, 5) is None
    assert doc.column_at_display(row, 5) is None
    assert doc.column_slice(row, 5, 10) == ""
    assert doc.has_char_at(row, 5) is False
    assert doc.display_column(row, 0) == 0
    assert doc.byte_offset(row, 0) == oracle_row_ranges(path.read_bytes())[row].start
    doc.close()


def test_capability_calls_read_bounded_windows_and_get_size_reads_nothing(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=60_000)
    source = CountingSource(path)
    doc = LazyDocument(source, _config())
    assert doc.wait_indexed(10.0)
    row = _long_row(doc)
    doc.long_index(row).join(10.0)
    source.owner_reads.clear()
    doc.column_slice(row, 30_000, 30_500)
    doc.display_column(row, 45_000)
    doc.column_at_display(row, 50_000)
    doc.byte_offset(row, 55_000)
    doc.has_char_at(row, 20_000)
    assert source.owner_reads
    assert max(source.owner_reads) <= MAX_WINDOW_CHARS * 4 + 8
    source.owner_reads.clear()
    size = doc.get_size(4)
    assert size.height == doc.line_count
    assert size.width > 0
    assert source.owner_reads == []
    doc.note_x(10_000_000)
    assert doc.get_size(4).width == 10_000_000
    doc.close()


def test_get_size_is_available_before_the_scan_completes(tmp_path: Path) -> None:
    doc = LazyDocument.from_path(make_mixed(tmp_path / "m.txt"), _config(), autostart=False)
    size = doc.get_size(4)
    assert (size.width, size.height) == (0, 1)
    doc.close()


def test_subscribe_reaches_long_indexes_created_later(tmp_path: Path) -> None:
    doc = LazyDocument.from_path(make_mixed(tmp_path / "m.txt", long_chars=20_000), _config())
    ticks = threading.Semaphore(0)
    doc.subscribe(ticks.release)
    assert doc.wait_indexed(10.0)
    while ticks.acquire(blocking=False):
        pass
    index = doc.long_index(_long_row(doc))
    assert index.join(10.0)
    assert ticks.acquire(timeout=10.0)
    doc.close()


def test_long_index_is_cached_and_lru_bounded(tmp_path: Path) -> None:
    path = tmp_path / "many.txt"
    path.write_bytes(b"\n".join(b"x" * 600 for _ in range(6)) + b"\nend")
    doc = _open(path, max_long_indexes=2)
    assert all(doc.is_long(row) for row in range(6))
    first = doc.long_index(0)
    assert doc.long_index(0) is first
    for row in range(1, 5):
        doc.long_index(row)
    assert doc.long_index(0) is not first
    doc.close()


def test_close_is_idempotent_with_running_scans(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=200_000)
    source = CountingSource(path)
    doc = LazyDocument(source, _config(yield_seconds=0.001))
    assert doc.wait_indexed(10.0)
    source.scan_delay = 0.3
    row = _long_row(doc)
    doc.column_slice(row, 0, 10)
    index = doc.long_index(row)
    assert index.frontier().complete is False
    doc.close()
    doc.close()
    assert index.frontier().complete is False
    with pytest.raises(ValueError, match="closed"):
        source.read(0, 1)


def test_close_during_line_scan(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=200_000)
    source = CountingSource(path)
    source.scan_delay = 0.3
    doc = LazyDocument(source, _config(yield_seconds=0.001))
    doc.close()
    doc.close()
    assert doc.snapshot().complete is False


def test_is_growing_until_every_scan_finished_and_false_after_close(tmp_path: Path) -> None:
    doc = LazyDocument(PreadSource(make_mixed(tmp_path / "m.txt", long_chars=6000)), LazyConfig(**LOWERED_OPTIONS), autostart=False)
    assert doc.is_growing()
    doc.start_scan()
    assert doc.wait_indexed(10)
    doc.long_index(12).join(10)
    deadline = time.monotonic() + 10
    while doc.is_growing() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not doc.is_growing()
    doc.close()
    assert not doc.is_growing()


class _SizeSpy:
    """`ByteSource` that records the size of every scan read (`cache=False`)."""

    def __init__(self, path: Path) -> None:
        self._inner = PreadSource(path)
        self.scan_sizes: list[int] = []

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if not cache:
            self.scan_sizes.append(size)
        return self._inner.read(offset, size, cache=cache)

    def close(self) -> None:
        self._inner.close()


def test_scan_block_defaults_to_the_core_default() -> None:
    assert LazyConfig().scan_block == DEFAULT_SCAN_BLOCK


def test_scan_block_limits_every_scan_read(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=4000)
    spy = _SizeSpy(path)
    doc = LazyDocument(spy, LazyConfig(**LOWERED_OPTIONS, scan_block=128))
    assert doc.wait_indexed(10.0)
    assert doc.long_index(_long_row(doc)).join(10.0)
    assert spy.scan_sizes
    assert max(spy.scan_sizes) <= 128
    doc.close()
