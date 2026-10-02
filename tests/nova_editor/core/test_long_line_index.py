"""Tests for the long-line checkpoint index (REQ-4, DEC-10)."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.core import byte_source
from nova_editor.core.byte_source import PreadSource, SourceChanged
from nova_editor.core.long_line_index import CHECKPOINT_CHARS, LongLineIndex

from .helpers import LONG_ATOMS, VALID_LONG_ATOMS, LongLineReference, MemorySource

FIRST_BLOCK = 16
REPLACEMENT = "�"
lines = st.lists(st.sampled_from(LONG_ATOMS), max_size=80).map(b"".join)
valid_lines = st.lists(st.sampled_from(VALID_LONG_ATOMS), max_size=80).map(b"".join)


def build(data: bytes, *, checkpoint: int, block: int, tab: int = 4) -> LongLineIndex:
    index = LongLineIndex(MemorySource(data), 0, len(data), tab_width=tab, checkpoint_chars=checkpoint, scan_block=block)
    assert index.wait_until_known(char_col=len(data) + 1, timeout=10) or index.frontier().complete
    return index


@given(data=lines, checkpoint=st.sampled_from([1, 2, 3, 7, 65536]), block=st.sampled_from([1, 2, 3, 5, 64, 1 << 20]), tab=st.sampled_from([1, 4, 8]))
@settings(deadline=None, max_examples=200)
def test_column_mappings_match_reference(data: bytes, checkpoint: int, block: int, tab: int) -> None:
    index = build(data, checkpoint=checkpoint, block=block, tab=tab)
    ref = LongLineReference(data, tab)
    size = len(ref.text)
    assert index.frontier().complete
    assert index.total_chars == size
    assert index.total_disp == ref.disp[-1]
    for col in range(size + 1):
        assert index.try_char_to_byte(col) == ref.byte[col]
        assert index.try_char_to_disp(col) == ref.disp[col]
    assert index.try_char_to_byte(size + 5) == ref.byte[-1]  # clamped to the line end
    for target in range(ref.disp[-1] + 3):
        assert index.try_disp_to_char(target) == ref.disp_to_char(target)
        assert index.try_disp_to_char(target, ceil=True) == ref.disp_to_char(target, ceil=True)
    for a in range(0, size + 1, 3):
        for b in range(a, min(size + 3, a + 9)):
            assert index.try_get_slice(a, b) == ref.text[a : min(b, a + checkpoint)]


@given(data=valid_lines, checkpoint=st.sampled_from([1, 3, 65536]), block=st.sampled_from([1, 2, 7, 1 << 20]))
@settings(deadline=None, max_examples=150)
def test_decode_from_arbitrary_byte_never_yields_replacement_characters(data: bytes, checkpoint: int, block: int) -> None:
    index = build(data, checkpoint=checkpoint, block=block)
    ref = LongLineReference(data)
    starts = set(ref.byte)
    for offset in range(len(data) + 1):
        text, skipped = index.decode_from_byte(offset, 5)
        assert REPLACEMENT not in text
        boundary = next(b for b in ref.byte if b >= offset)
        assert offset + skipped == boundary
        j = ref.byte.index(boundary)
        assert text == ref.text[j : j + 5]
        assert offset + skipped in starts


@given(data=lines)
@settings(deadline=None, max_examples=100)
def test_invalid_bytes_do_not_raise_in_decode_from_byte(data: bytes) -> None:
    index = build(data, checkpoint=3, block=4)
    for offset in range(len(data) + 1):
        text, _ = index.decode_from_byte(offset, 4)
        assert isinstance(text, str)


def test_tabs_wide_and_combining_widths() -> None:
    data = "a\t漢é\tz".encode()
    index = build(data, checkpoint=2, block=3)
    # a=1, tab->4, wide char=2 (6), e=1 (7), U+0301=0, tab->8, z=1 (9)
    expected_width = 9
    assert index.total_disp == expected_width
    ref = LongLineReference(data)
    assert index.total_disp == ref.disp[-1]


def test_far_column_query_does_not_block_while_the_scan_is_stalled() -> None:
    data = ("x" * 50 + "é" * 50).encode()
    gate = threading.Event()
    index = LongLineIndex(MemorySource(data, gate=gate), 0, len(data), checkpoint_chars=8, scan_block=16)
    try:
        began = time.perf_counter()
        assert index.try_char_to_byte(90) is None
        assert index.try_char_to_disp(90) is None
        assert index.try_disp_to_char(90) is None
        assert index.try_get_slice(80, 90) is None
        assert index.total_chars is None
        assert index.estimate_length() >= index.frontier().chars
        assert index.estimate_display_width() >= 0
        assert index.byte_to_char_approx(120) >= 0
        assert index.decode_from_byte(60, 4)[0] != ""  # a byte-based read needs no prefix
        assert time.perf_counter() - began < 0.01  # no non-blocking call waits for the scan
        assert index.wait_until_known(char_col=90, timeout=0.05) is False
        cancel = threading.Event()
        cancel.set()
        assert index.wait_until_known(char_col=90, timeout=5, cancel=cancel) is False
    finally:
        gate.set()
    assert index.wait_until_known(char_col=90, timeout=10)
    assert index.try_char_to_byte(90) == 50 + 40 * 2
    assert index.join(10)
    assert index.estimate_length() == index.total_chars == 100
    assert index.estimate_display_width() == index.total_disp
    index.cancel()


def test_a_wait_in_progress_is_ended_by_the_cancel_event() -> None:
    data = b"x" * 64
    gate = threading.Event()
    index = LongLineIndex(MemorySource(data, gate=gate), 0, len(data), checkpoint_chars=4, scan_block=16)
    cancel = threading.Event()
    timer = threading.Timer(0.05, cancel.set)
    try:
        timer.start()
        began = time.perf_counter()
        assert index.wait_until_known(char_col=60, timeout=10, cancel=cancel) is False
        assert time.perf_counter() - began < 2.0
    finally:
        timer.cancel()
        gate.set()
        index.cancel()
        index.join(5)


class StallAfterFirstBlock(MemorySource):
    """Lets the first scan block through, then stalls scan reads until `release` is set."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.release = threading.Event()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if not cache and offset >= FIRST_BLOCK:
            self.release.wait()
        return super().read(offset, size, cache=cache)


def test_try_queries_inside_the_frontier_answer_while_the_scan_is_stalled() -> None:
    data = ("x" * 64).encode()
    source = StallAfterFirstBlock(data)
    index = LongLineIndex(source, 0, len(data), checkpoint_chars=4, scan_block=FIRST_BLOCK)
    try:
        assert index.wait_until_known(char_col=FIRST_BLOCK, timeout=5)
        began = time.perf_counter()
        assert index.try_char_to_byte(10) == 10
        assert index.try_char_to_byte(40) is None
        assert time.perf_counter() - began < 0.05
        frontier = index.frontier()
        assert frontier.chars == FIRST_BLOCK
        assert not frontier.complete
        assert index.estimate_length() >= frontier.chars
    finally:
        source.release.set()
        index.cancel()


def test_subscribers_are_notified_on_progress_and_completion() -> None:
    data = b"x" * 100
    seen: list[int] = []
    index = LongLineIndex(MemorySource(data), 0, len(data), checkpoint_chars=10, scan_block=25, autostart=False)
    index.subscribe(lambda: seen.append(index.frontier().chars))
    index.start()
    assert index.wait_until_known(char_col=100, timeout=10)
    deadline = time.monotonic() + 5
    while (not seen or seen[-1] != len(data)) and time.monotonic() < deadline:
        time.sleep(0.005)
    assert seen[-1] == len(data)
    assert seen == sorted(seen)


def test_scan_error_is_raised_by_queries() -> None:
    class Failing(MemorySource):
        def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
            if not cache:
                raise SourceChanged("gone")
            return super().read(offset, size, cache=cache)

    index = LongLineIndex(Failing(b"abc"), 0, 3)
    with pytest.raises(SourceChanged):
        index.wait_until_known(char_col=2, timeout=5)
    with pytest.raises(SourceChanged):
        index.try_char_to_byte(1)


def test_checkpoint_count_and_spacing() -> None:
    data = b"x" * 100
    index = build(data, checkpoint=8, block=30)
    # every full block of 30 characters adds pieces of 8, 8, 8 and 6; the last block has 10 characters: 8 and 2
    assert index.checkpoint_count == 1 + 3 * 4 + 2
    assert CHECKPOINT_CHARS == 65_536


# -- try_get_slice bounds (design 6.3) -------------------------------------------------------------
def test_try_get_slice_is_bounded_to_one_checkpoint_interval() -> None:
    data = ("x" * 40).encode()
    index = build(data, checkpoint=8, block=1 << 20)
    assert index.try_get_slice(0, 10**9) == "x" * 8
    assert index.try_get_slice(5, 10**9) == "x" * 8
    assert index.try_get_slice(35, 10**9) == "x" * 5
    assert index.try_get_slice(40, 10**9) == ""
    assert index.try_get_slice(90, 10**9) == ""  # beyond a complete scan: clamped to the end


def test_try_get_slice_never_returns_text_beyond_the_frontier() -> None:
    data = ("x" * 64).encode()
    source = StallAfterFirstBlock(data)
    index = LongLineIndex(source, 0, len(data), checkpoint_chars=64, scan_block=FIRST_BLOCK)
    try:
        assert index.wait_until_known(char_col=FIRST_BLOCK, timeout=5)
        assert index.try_get_slice(0, 10**9) == "x" * FIRST_BLOCK
        assert index.try_get_slice(4, 30) == "x" * (FIRST_BLOCK - 4)
        assert index.try_get_slice(FIRST_BLOCK, 40) == ""  # at the frontier: nothing known yet
        assert index.try_get_slice(FIRST_BLOCK + 1, 40) is None
    finally:
        source.release.set()
        index.cancel()
        index.join(5)


def test_try_get_slice_empty_range_still_checks_the_frontier() -> None:
    data = ("x" * 64).encode()
    source = StallAfterFirstBlock(data)
    index = LongLineIndex(source, 0, len(data), checkpoint_chars=4, scan_block=FIRST_BLOCK)
    try:
        assert index.wait_until_known(char_col=FIRST_BLOCK, timeout=5)
        assert index.try_get_slice(3, 3) == ""
        assert index.try_get_slice(40, 40) is None
        assert index.try_get_slice(40, 10) is None
    finally:
        source.release.set()
        index.cancel()
        index.join(5)


# -- subscribe after a terminal state (I4) -----------------------------------------------------------
def test_late_subscriber_of_a_completed_scan_is_called_once() -> None:
    index = build(b"x" * 30, checkpoint=8, block=10)
    calls: list[bool] = []
    index.subscribe(lambda: calls.append(index.frontier().complete))
    assert calls == [True]


def test_late_subscriber_of_a_failed_scan_is_called_once() -> None:
    class Failing(MemorySource):
        def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
            if not cache:
                raise SourceChanged("gone")
            return super().read(offset, size, cache=cache)

    index = LongLineIndex(Failing(b"abc"), 0, 3)
    with pytest.raises(SourceChanged):
        index.wait_until_known(char_col=2, timeout=5)
    index.join(5)
    calls: list[int] = []
    index.subscribe(lambda: calls.append(1))
    assert calls == [1]


def test_subscriber_of_a_running_scan_is_not_called_at_subscription() -> None:
    gate = threading.Event()
    index = LongLineIndex(MemorySource(b"x" * 30, gate=gate), 0, 30, checkpoint_chars=8, scan_block=10)
    calls: list[int] = []
    try:
        index.subscribe(lambda: calls.append(1))
        assert calls == []
    finally:
        gate.set()
        index.cancel()
        index.join(5)


def test_raising_subscriber_is_contained_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    index = build(b"x" * 30, checkpoint=8, block=10)

    def bad() -> None:
        raise RuntimeError("subscriber failure")

    with caplog.at_level(logging.DEBUG, logger="nova_editor.core"):
        index.subscribe(bad)
    assert any(record.exc_info is not None and "subscriber" in record.getMessage() for record in caplog.records)


# -- join and close ordering (I2) --------------------------------------------------------------------
def test_join_reports_whether_the_scan_completed() -> None:
    index = LongLineIndex(MemorySource(b"x" * 30), 0, 30, checkpoint_chars=8, scan_block=10)
    assert index.join(10) is True
    gate = threading.Event()
    stalled = LongLineIndex(MemorySource(b"x" * 30, gate=gate), 0, 30, checkpoint_chars=8, scan_block=10)
    try:
        assert stalled.join(0.05) is False
    finally:
        gate.set()
        stalled.cancel()
    assert stalled.join(10) is False  # cancelled before the end


def test_close_after_cancel_waits_for_the_scan_read_and_leaves_no_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "long.txt"
    path.write_bytes(b"x" * 4096)
    source = PreadSource(path)
    entered = threading.Event()
    release = threading.Event()
    real = os.pread

    def slow(fd: int, size: int, offset: int) -> bytes:
        entered.set()
        release.wait()
        return real(fd, size, offset)

    monkeypatch.setattr(byte_source.os, "pread", slow)
    index = LongLineIndex(source, 0, 4096, checkpoint_chars=64, scan_block=512)
    closer = threading.Thread(target=source.close)
    try:
        assert entered.wait(5)  # the scan is inside a read
        index.cancel()
        closer.start()
        closer.join(0.2)
        assert closer.is_alive()  # close waits for the read in flight
    finally:
        release.set()
        closer.join(5)
        index.join(5)
    assert not closer.is_alive()
    # no SourceChanged (EBADF or a reused descriptor) was recorded by the scan: waiting raises nothing
    assert index.wait_until_known(char_col=10**6, timeout=0.05) is False


# -- SourceChanged from the index's own reads (I5) ---------------------------------------------------
QUERIES: dict[str, Callable[[LongLineIndex], object]] = {
    "try_char_to_byte": lambda index: index.try_char_to_byte(13),
    "try_char_to_disp": lambda index: index.try_char_to_disp(13),
    "try_disp_to_char": lambda index: index.try_disp_to_char(13),
    "try_get_slice": lambda index: index.try_get_slice(3, 13),
    "decode_from_byte": lambda index: index.decode_from_byte(13, 4),
    "byte_to_char_approx": lambda index: index.byte_to_char_approx(13),
}


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_queries_raise_source_changed_after_the_file_is_truncated(tmp_path: Path, name: str) -> None:
    path = tmp_path / "long.txt"
    path.write_bytes(b"abcdefgh" * 16)
    source = PreadSource(path)
    index = LongLineIndex(source, 0, 128, checkpoint_chars=8, scan_block=32)
    try:
        assert index.join(10)
        assert QUERIES[name](index) is not None
        os.truncate(path, 20)
        with pytest.raises(SourceChanged):
            QUERIES[name](index)
    finally:
        index.cancel()
        source.close()


# -- negative byte offsets (M2) ----------------------------------------------------------------------
def test_byte_to_char_approx_clamps_negative_offsets() -> None:
    index = build(("é" * 30).encode(), checkpoint=8, block=16)
    assert index.byte_to_char_approx(-5) == 0
    assert index.byte_to_char_approx(-(10**9)) == 0


def test_quiescent_is_true_only_after_the_scan_thread_has_ended() -> None:
    data = b"x" * 100
    gate = threading.Event()
    index = LongLineIndex(MemorySource(data, gate=gate), 0, len(data), checkpoint_chars=10, scan_block=25, autostart=False)
    assert not index.quiescent()  # not started: a start may still follow
    index.start()
    assert not index.quiescent()  # stalled in a read, thread alive
    index.cancel()
    assert not index.quiescent()  # cancelled, but the read in flight has not returned
    gate.set()
    index.join(10)
    assert index.quiescent()


def test_quiescent_is_false_while_the_thread_is_being_started(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inside `start`, before the thread runs, a started-but-not-yet-alive thread must not count as finished."""
    seen: list[bool] = []
    holder: list[LongLineIndex] = []
    real_start = threading.Thread.start

    def spying_start(thread: threading.Thread) -> None:
        if thread.name == "long-line-scan":
            seen.append(holder[0].quiescent())
        real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", spying_start)
    data = b"x" * 100
    index = LongLineIndex(MemorySource(data), 0, len(data), checkpoint_chars=10, scan_block=25, autostart=False)
    holder.append(index)
    index.start()
    assert seen == [False]
    assert index.join(10)
    assert index.quiescent()


def test_quiescent_after_a_completed_scan() -> None:
    data = b"x" * 100
    index = LongLineIndex(MemorySource(data), 0, len(data), checkpoint_chars=10, scan_block=25)
    assert index.join(10)
    assert index.quiescent()
