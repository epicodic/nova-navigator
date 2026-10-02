"""`LongLineIndex.rebased` against a freshly scanned index over an identical source (ACT5 design 7.3)."""

from __future__ import annotations

import threading

from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.core import BytesSource, LongLineIndex
from nova_editor.core.long_line_index import Edit

from .helpers import LongLineReference, MemorySource
from .test_long_line_splice import (
    TAB,
    TIMEOUT,
    Stepper,
    assert_equals_fresh,
    assert_known_answers_are_exact,
    build,
    rows,
)


class LoggedSource:
    """In-memory source that logs every `(offset, size)` read, so a test can see which bytes were read."""

    def __init__(self, data: bytes) -> None:
        self._inner = MemorySource(data)
        self.log: list[tuple[int, int]] = []
        self._lock = threading.Lock()

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        with self._lock:
            self.log.append((offset, size))
        return self._inner.read(offset, size, cache=cache)

    def close(self) -> None:
        self._inner.close()


@given(data=rows, step=st.sampled_from([1, 2, 3, 7]), block=st.sampled_from([1, 3, 64]))
@settings(deadline=None, max_examples=120)
def test_rebased_complete_index_equals_a_fresh_scan(data: bytes, step: int, block: int) -> None:
    old = build(data, step=step, block=block)
    count = old.checkpoint_count
    new = LongLineIndex.rebased(old, MemorySource(bytes(data)))
    assert new is not None
    assert new.join(TIMEOUT)
    assert new.checkpoint_count == count
    assert_equals_fresh(new, data, step)
    assert old.frontier().complete
    assert old.checkpoint_count == count


@given(data=rows, step=st.sampled_from([1, 2, 3, 7]), done=st.integers(min_value=0, max_value=40))
@settings(deadline=None, max_examples=60)
def test_rebased_index_is_exact_while_the_old_scan_is_stepped(data: bytes, step: int, done: int) -> None:
    stepper = Stepper(data)
    old = LongLineIndex(stepper, 0, len(data), tab_width=TAB, checkpoint_chars=step, scan_block=3)
    try:
        seen = 0
        for _ in range(done):
            if not stepper.wait_for_read_or_end(old, seen):
                break
            seen = stepper.arrived
            stepper.release()
        kept = old.checkpoint_count
        frontier = old.frontier()
        new = LongLineIndex.rebased(old, MemorySource(bytes(data)))
        assert new is not None
        assert new.checkpoint_count >= kept
        ref = LongLineReference(data, TAB)
        assert_known_answers_are_exact(new, ref, step)
        assert new.join(TIMEOUT)
        assert_equals_fresh(new, data, step)
        # the old index is untouched by the rebase and can still be cancelled and joined
        assert old.frontier().chars >= frontier.chars
        old.cancel()
        stepper.open_gate()
        old.join(TIMEOUT)
        assert old.quiescent()
    finally:
        stepper.open_gate()
        old.cancel()
        old.join(TIMEOUT)


def test_a_complete_index_does_not_rescan_below_its_last_checkpoint() -> None:
    data = ("abé\t漢" * 40).encode()
    old = build(data, step=7)
    last = old.frontier().byte_rel
    source = LoggedSource(data)
    new = LongLineIndex.rebased(old, source)
    assert new is not None
    assert new.join(TIMEOUT)
    assert new.checkpoint_count == old.checkpoint_count
    assert [read for read in source.log if read[0] < last] == []
    assert new.total_chars == old.total_chars
    assert_equals_fresh(new, data, 7)


def test_an_incomplete_index_resumes_from_its_frontier_on_the_new_source() -> None:
    data = ("abé\t漢" * 40).encode()
    stepper = Stepper(data)
    old = LongLineIndex(stepper, 0, len(data), tab_width=TAB, checkpoint_chars=7, scan_block=16)
    try:
        assert stepper.wait_for_read_or_end(old, 0)
        stepper.release()
        assert stepper.wait_for_read_or_end(old, 1)  # first block published, second read waits
        frontier = old.frontier().byte_rel
        assert 0 < frontier < len(data)
        source = LoggedSource(data)
        new = LongLineIndex.rebased(old, source, autostart=False)
        assert new is not None
        new.start()
        assert new.join(TIMEOUT)
        assert min(offset for offset, _ in source.log) >= frontier
        assert_equals_fresh(new, data, 7)
    finally:
        stepper.open_gate()
        old.cancel()
        old.join(TIMEOUT)


def test_rebased_returns_none_when_the_lengths_differ() -> None:
    data = b"abc\xe2\x82\xac" * 10
    old = build(data, step=3)
    assert LongLineIndex.rebased(old, BytesSource(data + b"x")) is None
    assert LongLineIndex.rebased(old, BytesSource(data[:-1])) is None


def test_rebased_empty_row_and_identity_edit_shape() -> None:
    old = build(b"", step=3)
    new = LongLineIndex.rebased(old, BytesSource(b""))
    assert new is not None
    assert new.join(TIMEOUT)
    assert new.total_chars == 0
    assert Edit(0, 0, 0, 0, 0, 0, 0).x_rel == 0
