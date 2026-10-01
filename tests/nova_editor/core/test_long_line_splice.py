"""`LongLineIndex.resume`, `byte_to_char` and `LongLineIndex.spliced` against a freshly scanned index (ACT4 design 6.2)."""

from __future__ import annotations

import threading

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from nova_editor.core import AddStore, BytesSource, LineIndex, LongLineIndex, PieceTable
from nova_editor.core.byte_source import ByteSource
from nova_editor.core.long_line_index import Edit
from nova_editor.core.text_width import advance_disp

from .helpers import LONG_ATOMS, LongLineReference, MemorySource

TAB = 4
SCAN_THREAD = "long-line-scan"
TIMEOUT = 10.0

rows = st.lists(st.sampled_from(LONG_ATOMS), max_size=60).map(b"".join)
inserts = st.lists(st.sampled_from(LONG_ATOMS), max_size=40).map(b"".join)


def build(data: bytes, *, step: int, block: int = 64) -> LongLineIndex:
    index = LongLineIndex(MemorySource(data), 0, len(data), tab_width=TAB, checkpoint_chars=step, scan_block=block)
    assert index.join(TIMEOUT)
    return index


def make_edit(old: bytes, col_a: int, col_b: int, inserted: bytes) -> tuple[Edit, bytes]:
    """Return the edit that replaces the characters `[col_a, col_b)` of `old` by `inserted`, and the new bytes; every measure comes from a full decode."""
    ref = LongLineReference(old, TAB)
    x, e = ref.byte[col_a], ref.byte[col_b]
    text = inserted.decode("utf-8", "surrogateescape")
    added_disp = advance_disp(text, ref.disp[col_a], TAB) - ref.disp[col_a]
    edit = Edit(x, e - x, col_b - col_a, ref.disp[col_b] - ref.disp[col_a], len(inserted), len(text), added_disp)
    return edit, old[:x] + inserted + old[e:]


def clean_junctions(old: bytes, edit: Edit, new: bytes) -> bool:
    """The edit decodes piecewise: prefix, inserted text and tail decode to the same characters as the new row."""
    x, e, added = edit.x_rel, edit.x_rel + edit.removed_bytes, edit.added_bytes
    parts = [old[:x], new[x : x + added], old[e:]]
    return "".join(part.decode("utf-8", "surrogateescape") for part in parts) == new.decode("utf-8", "surrogateescape")


def tab_alignment_kept(old: bytes, edit: Edit) -> bool:
    """Documented limit (design 6.4): tabs behind the edit keep their alignment only when the width change is a multiple of the tab width."""
    behind = old[edit.x_rel + edit.removed_bytes :]
    return b"\t" not in behind or (edit.added_disp - edit.removed_disp) % TAB == 0


class Stepper:
    """Byte source whose scan-thread reads wait for a permit, so a test advances the scan one read at a time without sleeping.

    Only reads made on the scan thread wait; the synchronous splice scan and the UI-path reads never do.
    """

    def __init__(self, data: bytes) -> None:
        self._inner = BytesSource(data)
        self._cond = threading.Condition()
        self.arrived = 0
        self._permits = 0
        self._open = False

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if not cache and threading.current_thread().name == SCAN_THREAD:
            with self._cond:
                self.arrived += 1
                self._cond.notify_all()
                assert self._cond.wait_for(lambda: self._permits > 0 or self._open, TIMEOUT)
                if not self._open:
                    self._permits -= 1
        return self._inner.read(offset, size, cache=cache)

    def close(self) -> None:
        self._inner.close()

    def release(self) -> None:
        with self._cond:
            self._permits += 1
            self._cond.notify_all()

    def open_gate(self) -> None:
        """Let every later read through (a finished or failed test must not leave the scan thread waiting)."""
        with self._cond:
            self._open = True
            self._cond.notify_all()

    def wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def wait_for_read_or_end(self, index: LongLineIndex, seen: int) -> bool:
        """Wait until the scan thread waits in a read beyond the `seen`-th (true) or the scan completed (false)."""
        index.subscribe(self.wake)
        with self._cond:
            assert self._cond.wait_for(lambda: self.arrived > seen or index.frontier().complete, TIMEOUT)
            return self.arrived > seen and not index.frontier().complete


def settle(stepper: Stepper, index: LongLineIndex) -> None:
    """Run the scan to the end, one read at a time."""
    seen = 0
    while stepper.wait_for_read_or_end(index, seen):
        seen = stepper.arrived
        stepper.release()
    stepper.open_gate()
    assert index.join(TIMEOUT)


def assert_known_answers_are_exact(index: LongLineIndex, ref: LongLineReference, step: int) -> None:
    """Every answer that is not `None` equals the answer of the finished scan; columns inside the frontier are never `None`."""
    size = len(ref.text)
    known = index.frontier().chars
    for col in range(size + 1):
        byte = index.try_char_to_byte(col)
        disp = index.try_char_to_disp(col)
        assert byte in (None, ref.byte[col])
        assert disp in (None, ref.disp[col])
        if col <= known:
            assert byte is not None
            assert disp is not None
    for target in range(ref.disp[-1] + 2):
        for ceil in (False, True):
            assert index.try_disp_to_char(target, ceil=ceil) in (None, ref.disp_to_char(target, ceil=ceil))
    for a in range(0, size + 1, 2):
        got = index.try_get_slice(a, a + 5)
        assert got is None or ref.text[a : a + min(5, step)].startswith(got)
    encoded = ref.text.encode("utf-8", "surrogateescape")
    for rel in range(0, len(encoded) + 1, 2):
        assert index.byte_to_char(rel) in (None, len(encoded[:rel].decode("utf-8", "surrogateescape")))
    assert index.total_chars in (None, size)
    assert index.total_disp in (None, ref.disp[-1])
    assert index.estimate_length() >= 0
    assert index.estimate_display_width() >= 0


def assert_equals_fresh(index: LongLineIndex, new: bytes, step: int) -> None:
    """After completion every query equals the same query on a freshly scanned index of the edited row."""
    fresh = build(new, step=step)
    ref = LongLineReference(new, TAB)
    size = len(ref.text)
    assert index.frontier().complete
    assert (index.total_chars, index.total_disp) == (fresh.total_chars, fresh.total_disp) == (size, ref.disp[-1])
    assert index.estimate_length() == fresh.estimate_length()
    assert index.estimate_display_width() == fresh.estimate_display_width()
    for col in range(size + 3):
        assert index.try_char_to_byte(col) == fresh.try_char_to_byte(col)
        assert index.try_char_to_disp(col) == fresh.try_char_to_disp(col)
        assert index.byte_to_char(col) == fresh.byte_to_char(col)
    for target in range(ref.disp[-1] + 3):
        for ceil in (False, True):
            assert index.try_disp_to_char(target, ceil=ceil) == fresh.try_disp_to_char(target, ceil=ceil)
    for a in range(size + 2):
        for b in (a, a + 1, a + step, a + step + 3):
            assert index.try_get_slice(a, b) == fresh.try_get_slice(a, b)
    assert index.byte_to_char(len(new) + 9) == size


# -- resume and byte_to_char ---------------------------------------------------------------------------------------------------------------


@given(data=rows, step=st.sampled_from([1, 2, 3, 7]), block=st.sampled_from([1, 3, 64]))
@settings(deadline=None, max_examples=120)
def test_byte_to_char_is_exact_for_every_byte(data: bytes, step: int, block: int) -> None:
    index = build(data, step=step, block=block)
    for rel in range(-1, len(data) + 3):
        expected = len(data[: max(rel, 0)].decode("utf-8", "surrogateescape"))
        assert index.byte_to_char(rel) == expected


def test_byte_to_char_is_none_beyond_the_frontier_until_complete() -> None:
    data = b"abcdefghij" * 4
    stepper = Stepper(data)
    index = LongLineIndex(stepper, 0, len(data), tab_width=TAB, checkpoint_chars=4, scan_block=8)
    try:
        stepper.release()
        assert stepper.wait_for_read_or_end(index, 1)  # waits in the second read, so one block (8 bytes) is published
        assert index.frontier().byte_rel == 8
        assert index.byte_to_char(8) == 8
        assert index.byte_to_char(5) == 5
        assert index.byte_to_char(9) is None
    finally:
        stepper.open_gate()
        index.cancel()
        index.join(TIMEOUT)


@given(data=rows, step=st.sampled_from([1, 3, 7]), block=st.sampled_from([1, 5, 64]), pick=st.integers(0, 1000))
@settings(deadline=None, max_examples=120)
def test_resume_continues_from_a_checkpoint_state(data: bytes, step: int, block: int, pick: int) -> None:
    ref = LongLineReference(data, TAB)
    col = pick % (len(ref.text) + 1)
    index = LongLineIndex(MemorySource(data), 0, len(data), tab_width=TAB, checkpoint_chars=step, scan_block=block, resume=(col, ref.disp[col], ref.byte[col]))
    assert index.join(TIMEOUT)
    assert_equals_fresh(index, data, step)


def test_resume_rejects_a_point_outside_the_row() -> None:
    with pytest.raises(ValueError, match="resume"):
        LongLineIndex(MemorySource(b"abc"), 0, 3, resume=(9, 9, 9), autostart=False)


# -- splice ----------------------------------------------------------------------------------------------------------------------------------


@st.composite
def splice_cases(draw: st.DrawFn) -> tuple[bytes, Edit, bytes]:
    old = draw(rows)
    size = len(old.decode("utf-8", "surrogateescape"))
    col_a = draw(st.integers(0, size))
    col_b = draw(st.integers(col_a, size))
    edit, new = make_edit(old, col_a, col_b, draw(inserts))
    assume(clean_junctions(old, edit, new))
    assume(tab_alignment_kept(old, edit))
    return old, edit, new


@given(case=splice_cases(), step=st.sampled_from([1, 2, 3, 7]), block=st.sampled_from([1, 3, 5, 64]), sync=st.sampled_from([0, 3, 1 << 20]))
@settings(deadline=None, max_examples=250)
def test_spliced_index_equals_a_fresh_scan_after_completion(case: tuple[bytes, Edit, bytes], step: int, block: int, sync: int) -> None:
    old_data, edit, new = case
    old = build(old_data, step=step, block=block)
    index = LongLineIndex.spliced(old, MemorySource(new), edit, sync_bytes=sync)
    assert index.join(TIMEOUT)
    assert_equals_fresh(index, new, step)


@given(case=splice_cases(), step=st.sampled_from([1, 2, 3]), sync=st.sampled_from([0, 3, 1 << 20]), done=st.integers(0, 12))
@settings(deadline=None, max_examples=120)
def test_spliced_index_is_exact_at_every_step_of_the_scan(case: tuple[bytes, Edit, bytes], step: int, sync: int, done: int) -> None:
    """The old index may be partly scanned (frontier before, inside or behind the edit); the new scan is stepped one read at a time."""
    old_data, edit, new = case
    old_source = Stepper(old_data)
    old = LongLineIndex(old_source, 0, len(old_data), tab_width=TAB, checkpoint_chars=step, scan_block=5)
    new_source = Stepper(new)
    index: LongLineIndex | None = None
    try:
        settled = 0
        for _ in range(done):
            if not old_source.wait_for_read_or_end(old, settled):
                break
            settled = old_source.arrived
            old_source.release()
        old_source.wait_for_read_or_end(old, settled)  # the old scan now waits in its next read (or is complete): its state is settled
        index = LongLineIndex.spliced(old, new_source, edit, sync_bytes=sync, scan_block=4)
        ref = LongLineReference(new, TAB)
        seen = 0
        while True:
            assert_known_answers_are_exact(index, ref, step)
            if not new_source.wait_for_read_or_end(index, seen):
                break
            assert_known_answers_are_exact(index, ref, step)
            seen = new_source.arrived
            new_source.release()
        new_source.open_gate()
        assert index.join(TIMEOUT)
        assert_equals_fresh(index, new, step)
    finally:
        old_source.open_gate()
        new_source.open_gate()
        old.cancel()
        old.join(TIMEOUT)
        if index is not None:
            index.cancel()
            index.join(TIMEOUT)


@pytest.mark.parametrize("sync", [0, 6, 1 << 20], ids=["background", "small-sync", "large-sync"])
@pytest.mark.parametrize("insert_chars", [0, 2, 3, 4, 9, 50], ids=lambda n: f"insert{n}")
def test_insert_sizes_around_one_step_and_the_sync_limit(insert_chars: int, sync: int) -> None:
    step = 4
    old_data = ("ab漢é" * 11).encode()
    inserted = ("xé" * insert_chars)[:insert_chars].encode()
    edit, new = make_edit(old_data, 7, 19, inserted)
    old = build(old_data, step=step, block=3)
    index = LongLineIndex.spliced(old, MemorySource(new), edit, sync_bytes=sync)
    assert index.join(TIMEOUT)
    assert_equals_fresh(index, new, step)


def test_synchronous_splice_is_known_immediately_and_background_splice_has_a_gap() -> None:
    step = 4
    old_data = b"abcdefgh" * 10
    inserted = b"0123456789" * 3
    edit, new = make_edit(old_data, 10, 12, inserted)
    old = build(old_data, step=step)
    size = len(new)

    sync = LongLineIndex.spliced(old, MemorySource(new), edit, autostart=False)
    assert sync.frontier().complete
    assert sync.try_char_to_byte(size) == len(new)
    assert sync.checkpoint_count > 5  # interior checkpoints of the inserted text

    stepper = Stepper(new)
    gap = LongLineIndex.spliced(old, stepper, edit, sync_bytes=4, scan_block=5)
    try:
        assert not gap.frontier().complete
        assert gap.frontier().byte_rel == edit.x_rel
        assert gap.try_char_to_byte(10) == edit.x_rel
        assert gap.try_char_to_byte(11) is None  # inside the gap
        assert gap.try_char_to_byte(size) is None  # behind the gap
        assert gap.total_chars is None
        settle(stepper, gap)
        assert_equals_fresh(gap, new, step)
    finally:
        stepper.open_gate()
        gap.cancel()
        gap.join(TIMEOUT)


def test_prefix_is_kept_and_tail_checkpoints_are_shifted_by_the_deltas() -> None:
    step = 4
    old_data = b"abcdefgh" * 8
    edit, new = make_edit(old_data, 17, 25, b"XYZ")
    old = build(old_data, step=step)
    index = LongLineIndex.spliced(old, MemorySource(new), edit)
    assert index.join(TIMEOUT)
    old_points = list(zip(old._cp_chars, old._cp_disp, old._cp_byte, strict=True))
    new_points = list(zip(index._cp_chars, index._cp_disp, index._cp_byte, strict=True))
    assert [p for p in old_points if p[2] < edit.x_rel] == [p for p in new_points if p[2] < edit.x_rel]
    assert (17, 17, 17) in new_points  # exact checkpoint at the edit
    assert (20, 20, 20) in new_points  # exact checkpoint at the end of the inserted text
    assert not any(17 < p[2] < 20 for p in new_points)  # the 3 inserted characters are below one step; the removed range is dropped
    shifted = [(c + 5, d + 5, b + 5) for c, d, b in new_points if b > 20]
    assert shifted == [p for p in old_points if p[2] > 25]
    assert len(shifted) > 0


def test_frontier_before_the_edit_keeps_only_the_prefix_and_resumes() -> None:
    step = 4
    old_data = b"abcdefgh" * 10
    stepper = Stepper(old_data)
    old = LongLineIndex(stepper, 0, len(old_data), tab_width=TAB, checkpoint_chars=step, scan_block=8)
    try:
        stepper.release()
        stepper.release()
        assert stepper.wait_for_read_or_end(old, 2)  # three reads arrived: two blocks (16 bytes) are published
        assert old.frontier().byte_rel == 16
        edit, new = make_edit(old_data, 40, 44, b"Q")
        index = LongLineIndex.spliced(old, MemorySource(new), edit, autostart=False)
        assert index.frontier().byte_rel == 16
        assert index.checkpoint_count == old.checkpoint_count
        assert index.try_char_to_byte(20) is None
        index.start()
        assert index.join(TIMEOUT)
        assert_equals_fresh(index, new, step)
    finally:
        stepper.open_gate()
        old.cancel()
        old.join(TIMEOUT)


def test_spliced_index_is_a_normal_index() -> None:
    old_data = b"abcdefgh" * 10
    edit, new = make_edit(old_data, 3, 5, b"12345678901234567890")
    old = build(old_data, step=4)
    events: list[bool] = []
    index = LongLineIndex.spliced(old, MemorySource(new), edit, autostart=False, sync_bytes=0)
    index.subscribe(lambda: events.append(True))
    assert index.running  # not started yet
    index.start()
    with pytest.raises(RuntimeError):
        index.start()
    assert index.join(TIMEOUT)
    assert events
    late: list[bool] = []
    index.subscribe(lambda: late.append(True))
    assert late == [True]
    assert (index.start_offset, index.end_offset) == (0, len(new))
    assert not index.running

    cancelled = LongLineIndex.spliced(old, MemorySource(new), edit, sync_bytes=0, autostart=False)
    cancelled.cancel()
    cancelled.start()
    cancelled.join(TIMEOUT)
    assert not cancelled.running
    assert not cancelled.frontier().complete


def test_emptied_row_and_edits_at_the_row_end() -> None:
    old_data = b"abcdefghij"
    old = build(old_data, step=3)
    edit, new = make_edit(old_data, 0, 10, b"")
    emptied = LongLineIndex.spliced(old, MemorySource(new), edit)
    assert emptied.join(TIMEOUT)
    assert (emptied.total_chars, emptied.total_disp) == (0, 0)
    for sync in (0, 1 << 20):
        edit, new = make_edit(old_data, 10, 10, b"klmnopqrstuvw")
        appended = LongLineIndex.spliced(old, MemorySource(new), edit, sync_bytes=sync)
        assert appended.join(TIMEOUT)
        assert_equals_fresh(appended, new, 3)
        edit, new = make_edit(old_data, 4, 10, b"klmnopqrstuvw")
        replaced = LongLineIndex.spliced(old, MemorySource(new), edit, sync_bytes=sync)
        assert replaced.join(TIMEOUT)
        assert_equals_fresh(replaced, new, 3)


def test_wrong_edit_measures_fail_the_index_instead_of_corrupting_it() -> None:
    old_data = b"abcdefgh" * 4
    old = build(old_data, step=3)
    edit, new = make_edit(old_data, 4, 6, b"0123456789")
    wrong = edit._replace(added_chars=edit.added_chars + 1)
    with pytest.raises(ValueError, match="does not describe"):
        LongLineIndex.spliced(old, MemorySource(new), wrong)
    background = LongLineIndex.spliced(old, MemorySource(new), wrong, sync_bytes=0)
    background.join(TIMEOUT)
    with pytest.raises(ValueError, match="does not describe"):
        background.try_char_to_byte(1)


def test_documented_tab_limit_behind_an_edit() -> None:
    """Design 6.4: with a tab behind the edit and a width change that is not a multiple of the tab width the columns are off by less than one tab stop."""
    old_data = b"ab\tcd\tef" * 3
    step = 3
    old = build(old_data, step=step)
    edit, new = make_edit(old_data, 0, 0, b"x")
    assert not tab_alignment_kept(old_data, edit)
    index = LongLineIndex.spliced(old, MemorySource(new), edit)
    assert index.join(TIMEOUT)
    fresh = build(new, step=step)
    assert index.total_disp is not None
    assert fresh.total_disp is not None
    assert index.total_disp != fresh.total_disp
    assert abs(index.total_disp - fresh.total_disp) < TAB
    assert index.total_chars == fresh.total_chars
    for col in range(index.total_chars or 0):
        assert index.try_char_to_byte(col) == fresh.try_char_to_byte(col)  # bytes and characters stay exact
    # With a width change that is a multiple of the tab width the same row is exact.
    edit, new = make_edit(old_data, 0, 0, b"wxyz")
    aligned = LongLineIndex.spliced(old, MemorySource(new), edit)
    assert aligned.join(TIMEOUT)
    assert_equals_fresh(aligned, new, step)


# -- threads ---------------------------------------------------------------------------------------------------------------------------------


class Interleaved:
    """Wraps a `ByteSource`; each scan-thread read first announces itself and waits until the main thread released it."""

    def __init__(self, inner: ByteSource) -> None:
        self._inner = inner
        self._cond = threading.Condition()
        self.arrived = 0
        self.released = 0

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if not cache and threading.current_thread().name == SCAN_THREAD:
            with self._cond:
                self.arrived += 1
                self._cond.notify_all()
                assert self._cond.wait_for(lambda: self.released >= self.arrived, TIMEOUT)
        return self._inner.read(offset, size, cache=cache)

    def close(self) -> None:
        self._inner.close()

    def wait_for_read(self, count: int) -> bool:
        """Wait until `count` reads arrived."""
        with self._cond:
            return self._cond.wait_for(lambda: self.arrived >= count, TIMEOUT)

    def release_all(self) -> None:
        with self._cond:
            self.released = 1 << 30
            self._cond.notify_all()

    def release_one(self) -> None:
        with self._cond:
            self.released += 1
            self._cond.notify_all()


def test_a_scan_thread_reads_a_row_source_while_the_table_is_edited() -> None:
    original = ("ab漢é\t" * 30).encode()
    source = BytesSource(original)
    line_index = LineIndex(source)
    line_index.scan_now()
    table = PieceTable(source, line_index, AddStore(tail_limit=16))
    table.splice(10, 10, table.add(b"typed "))
    table.splice(40, 45, table.add(b"x" * 40))
    snapshot = table.read(0, table.length)
    rows_source = table.row_source(0, table.length)
    gated = Interleaved(rows_source)
    index = LongLineIndex(gated, 0, rows_source.length(), tab_width=TAB, checkpoint_chars=5, scan_block=16)
    reads = -(-rows_source.length() // 16)
    try:
        position = 0
        for read in range(1, reads + 1):
            assert gated.wait_for_read(read)  # the scan thread waits inside its read; the table changes underneath it
            position = (position + 37) % (table.length - 3)
            table.splice(position, position, table.add(b"k"))  # typing continues the add tail segment the scan reads
            table.splice(position, position + 2, table.add(b"R" * 20))  # a sealed segment of its own
            table.splice(0, 3, table.add(b"ZZ"))
            gated.release_one()
        gated.release_all()
        assert index.join(TIMEOUT)
    finally:
        gated.release_all()
        index.cancel()
        index.join(TIMEOUT)
    assert table.read(0, table.length) != snapshot
    assert rows_source.read(0, rows_source.length()) == snapshot
    assert_equals_fresh(index, snapshot, 5)


def test_splicing_while_the_old_scan_thread_still_runs() -> None:
    """The old index scans on its own thread while the new one is built from its checkpoints; both end up exact."""
    old_data = ("abcde漢" * 40).encode()
    old_source = Interleaved(BytesSource(old_data))
    old = LongLineIndex(old_source, 0, len(old_data), tab_width=TAB, checkpoint_chars=4, scan_block=9)
    try:
        for count in range(1, 6):
            assert old_source.wait_for_read(count)
            old_source.release_one()
        assert old_source.wait_for_read(6)
        edit, new = make_edit(old_data, 30, 34, b"0123456789" * 3)
        index = LongLineIndex.spliced(old, BytesSource(new), edit, sync_bytes=0)
        old_source.release_all()
        assert old.join(TIMEOUT)
        assert index.join(TIMEOUT)
        assert_equals_fresh(index, new, 4)
    finally:
        old_source.release_all()
        old.cancel()
        old.join(TIMEOUT)
