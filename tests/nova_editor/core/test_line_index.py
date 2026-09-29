"""Tests for the sparse line index: semantics, block edges, non-blocking behaviour."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.core.byte_source import PreadSource, SourceChanged
from nova_editor.core.line_index import LineIndex, RowRange

from .helpers import ATOMS, MemorySource, reference_rows

files = st.lists(st.sampled_from(ATOMS), max_size=60).map(b"".join)


def build(data: bytes, *, stride: int = 64, scan_block: int = 1 << 20) -> LineIndex:
    index = LineIndex(MemorySource(data), stride=stride, scan_block=scan_block)
    index.start()
    assert index.join(10)
    return index


@given(data=files, stride=st.sampled_from([1, 2, 3, 64]), block=st.sampled_from([1, 2, 3, 5, 8, 1 << 20]))
@settings(deadline=None, max_examples=300)
def test_rows_match_reference(data: bytes, stride: int, block: int) -> None:
    index = build(data, stride=stride, scan_block=block)
    rows = reference_rows(data)
    snap = index.snapshot()
    assert snap.complete
    assert snap.error is None
    assert snap.count == len(rows)
    for row, (start, content_end, end) in enumerate(rows):
        assert index.row_range(row) == RowRange(start, content_end, end)
    assert index.lines(0, len(rows)) == [RowRange(*r) for r in rows]
    assert index.row_range(len(rows)) is None


@given(data=files, stride=st.sampled_from([1, 2, 3]), block=st.sampled_from([1, 2, 3, 7]), first=st.integers(0, 70), count=st.integers(0, 70))
@settings(deadline=None, max_examples=200)
def test_lines_batches_match_reference(data: bytes, stride: int, block: int, first: int, count: int) -> None:
    index = build(data, stride=stride, scan_block=block)
    rows = reference_rows(data)
    assert index.lines(first, count) == [RowRange(*r) for r in rows[first : first + count]]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", [(0, 0, 0)]),
        (b"a", [(0, 1, 1)]),
        (b"a\n", [(0, 1, 2), (2, 2, 2)]),
        (b"a\r\nb", [(0, 1, 3), (3, 4, 4)]),
        (b"a\rb", [(0, 1, 2), (2, 3, 3)]),
        (b"a\r", [(0, 1, 2), (2, 2, 2)]),
        (b"\r\n\r\n", [(0, 0, 2), (2, 2, 4), (4, 4, 4)]),
        (b"a\xe2\x80\xa8b", [(0, 5, 5)]),  # U+2028 is content
        (b"a\x0bb\xc2\x85c", [(0, 6, 6)]),  # VT and NEL are content
        (b"\xef\xbb\xbfa\n", [(0, 4, 5), (5, 5, 5)]),  # BOM is content of row 0
    ],
)
def test_known_cases(data: bytes, expected: list[tuple[int, int, int]]) -> None:
    for block in (1, 2, 1 << 20):
        index = build(data, scan_block=block)
        assert index.snapshot().count == len(expected)
        assert index.lines(0, 10) == [RowRange(*r) for r in expected]


def test_crlf_split_across_scan_blocks_is_one_terminator() -> None:
    data = b"ab\r\ncd\r\nef"
    index = build(data, scan_block=3)  # blocks: "ab\r" | "\ncd" | "\r\ne" | "f"
    assert index.snapshot().count == 3
    assert index.row_range(0) == RowRange(0, 2, 4)
    assert index.row_range(1) == RowRange(4, 6, 8)
    assert index.row_range(2) == RowRange(8, 10, 10)


def test_terminator_length_property() -> None:
    assert RowRange(0, 1, 3).terminator_length == 2
    assert RowRange(0, 1, 2).terminator_length == 1
    assert RowRange(0, 1, 1).terminator_length == 0


def test_negative_row_raises() -> None:
    index = build(b"a\nb")
    with pytest.raises(IndexError):
        index.row_range(-1)
    with pytest.raises(IndexError):
        index.lines(-1, 3)


def test_snapshot_grows_monotonically_and_lower_bound_holds() -> None:
    data = b"line\n" * 2000
    source = MemorySource(data)
    index = LineIndex(source, scan_block=64)
    seen: list[int] = []
    index.subscribe(lambda: seen.append(index.snapshot().count))
    index.start()
    assert index.join(10)
    assert seen == sorted(seen)
    assert all(count <= 2001 for count in seen)
    assert index.snapshot().count == 2001


def test_rows_past_the_frontier_return_none_immediately() -> None:
    gate = threading.Event()
    index = LineIndex(MemorySource(b"a\nb\nc\n", gate=gate), scan_block=2)
    index.start()
    try:
        began = time.perf_counter()
        assert index.snapshot().count == 1
        assert not index.snapshot().complete
        assert index.row_range(1) is None
        assert index.row_range(0) is None  # row 0 is open: its terminator is not scanned yet
        assert index.lines(0, 5) == []
        assert time.perf_counter() - began < 0.05
    finally:
        gate.set()
    assert index.join(10)
    assert index.row_range(1) == RowRange(2, 3, 4)


def test_subscriber_called_and_raising_subscriber_does_not_stop_scan() -> None:
    calls: list[int] = []
    index = LineIndex(MemorySource(b"a\n" * 50), scan_block=8)

    def bad() -> None:
        raise RuntimeError("subscriber failure")

    index.subscribe(bad)
    index.subscribe(lambda: calls.append(1))
    index.start()
    assert index.join(10)
    assert index.snapshot().complete
    assert len(calls) >= 2  # progress and completion


def test_cancel_leaves_a_lower_bound() -> None:
    gate = threading.Event()
    index = LineIndex(MemorySource(b"a\n" * 100, gate=gate), scan_block=4)
    try:
        index.start()
        index.cancel()
    finally:
        gate.set()
    index.join(10)
    snap = index.snapshot()
    assert not snap.complete
    assert snap.count <= 101


def test_source_changed_is_recorded_and_complete_stays_false(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"a\n" * 1000)
    source = PreadSource(path)
    index = LineIndex(source, scan_block=16)
    path.write_bytes(b"a\n" * 10)  # truncate before the scan starts
    index.start()
    index.join(10)
    snap = index.snapshot()
    assert isinstance(snap.error, SourceChanged)
    assert not snap.complete


def test_row_range_on_a_real_file(tmp_path: Path) -> None:
    data = b"one\r\ntwo\nthree\rfour"
    path = tmp_path / "f"
    path.write_bytes(data)
    index = LineIndex(PreadSource(path))
    index.start()
    assert index.join(10)
    assert [index.row_range(r) for r in range(4)] == [RowRange(*r) for r in reference_rows(data)]


def test_walk_on_a_shrunk_source_raises_instead_of_looping() -> None:
    source = MemorySource(b"a\nb\nc\n")
    index = LineIndex(source, scan_block=2)
    index.start()
    assert index.join(10)
    source._data = b""  # simulate truncation after the scan
    with pytest.raises(SourceChanged):
        index.row_range(1)


class _FailingScanSource(MemorySource):
    """Fails every scan read (`cache=False`) with a RuntimeError."""

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if not cache:
            raise RuntimeError("scan read failed")
        return super().read(offset, size, cache=cache)


def test_unexpected_scan_error_is_recorded_and_subscribers_are_notified() -> None:
    index = LineIndex(_FailingScanSource(b"a\nb\n"))
    called = threading.Event()
    index.subscribe(called.set)
    previous_hook = threading.excepthook
    threading.excepthook = lambda _args: None  # the scan thread re-raises on purpose
    try:
        index.start()
        index.join(10)
    finally:
        threading.excepthook = previous_hook
    snap = index.snapshot()
    assert isinstance(snap.error, RuntimeError)
    assert not snap.complete
    assert called.is_set()


def test_late_subscriber_of_a_completed_scan_is_called_once() -> None:
    index = build(b"a\n" * 50, scan_block=8)
    calls: list[bool] = []
    index.subscribe(lambda: calls.append(index.snapshot().complete))
    assert calls == [True]


def test_late_subscriber_of_a_failed_scan_is_called_once() -> None:
    index = LineIndex(_FailingScanSource(b"a\nb\n"))
    previous_hook = threading.excepthook
    threading.excepthook = lambda _args: None  # the scan thread re-raises on purpose
    try:
        index.start()
        index.join(10)
    finally:
        threading.excepthook = previous_hook
    calls: list[int] = []
    index.subscribe(lambda: calls.append(1))
    assert calls == [1]


def test_subscriber_of_a_running_scan_is_not_called_at_subscription() -> None:
    gate = threading.Event()
    index = LineIndex(MemorySource(b"a\n" * 50, gate=gate), scan_block=8)
    calls: list[int] = []
    try:
        index.subscribe(lambda: calls.append(1))
        assert calls == []
    finally:
        gate.set()
        index.cancel()
        index.join(10)


def test_raising_subscriber_is_contained_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    index = build(b"a\n" * 50, scan_block=8)

    def bad() -> None:
        raise RuntimeError("subscriber failure")

    with caplog.at_level(logging.DEBUG, logger="nova_editor.core"):
        index.subscribe(bad)
    assert any(record.exc_info is not None and "subscriber" in record.getMessage() for record in caplog.records)


def test_lines_and_row_range_raise_source_changed_after_the_file_is_truncated(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"one\ntwo\nthree\nfour\n" * 10)
    source = PreadSource(path)
    index = LineIndex(source, stride=2, scan_block=16)
    try:
        index.start()
        assert index.join(10)
        assert len(index.lines(0, 5)) == 5
        os.truncate(path, 5)
        with pytest.raises(SourceChanged):
            index.lines(0, 5)
        with pytest.raises(SourceChanged):
            index.row_range(3)
    finally:
        source.close()
