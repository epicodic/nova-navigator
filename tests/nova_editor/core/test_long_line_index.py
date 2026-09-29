"""Tests for the long-line checkpoint index (REQ-4, DEC-10)."""

from __future__ import annotations

import threading
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.core.byte_source import SourceChanged
from nova_editor.core.long_line_index import LongLineIndex

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
            assert index.try_get_slice(a, b) == ref.text[a:b]


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
    began = time.perf_counter()
    assert index.try_char_to_byte(90) is None
    assert index.try_char_to_disp(90) is None
    assert index.try_disp_to_char(90) is None
    assert index.try_get_slice(80, 90) is None
    assert index.total_chars is None
    assert index.estimate_length() >= 0
    assert index.estimate_display_width() >= 0
    assert index.byte_to_char_approx(120) >= 0
    assert index.wait_until_known(char_col=90, timeout=0.05) is False
    cancel = threading.Event()
    cancel.set()
    assert index.wait_until_known(char_col=90, timeout=5, cancel=cancel) is False
    assert time.perf_counter() - began < 1.0  # every call above is bounded: no call waits for the scan
    assert index.decode_from_byte(60, 4)[0] != ""  # a byte-based read needs no prefix
    gate.set()
    assert index.wait_until_known(char_col=90, timeout=10)
    assert index.try_char_to_byte(90) == 50 + 40 * 2
    index.cancel()


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
    assert index.wait_until_known(char_col=FIRST_BLOCK, timeout=5)
    began = time.perf_counter()
    assert index.try_char_to_byte(10) == 10
    assert index.try_char_to_byte(40) is None
    assert time.perf_counter() - began < 0.05
    frontier = index.frontier()
    assert frontier.chars == FIRST_BLOCK
    assert not frontier.complete
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
