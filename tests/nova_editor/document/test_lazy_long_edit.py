"""Long-row indexes over edits and the long-row document fuzz (ACT4 Task 8, design 6.1 to 6.3)."""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from nova_editor.core import LongLineIndex, RowSource
from nova_editor.core.text_width import advance_disp, utf8_len
from nova_editor.document import _lazy_document
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import MAX_WINDOW_CHARS, LazyDocument, index_step
from tests.nova_editor.core.reference import ALPHABET, Rng
from tests.nova_editor.document.reference_text import RefText, decode
from tests.nova_editor.document.test_lazy_edit import assert_same, both, settle

CONFIG = LazyConfig(stride=4, index_long_line_threshold=64, long_row_threshold=512, word_wrap_limit=128, checkpoint_chars=64)
TAB = 4
PLAIN = [atom for atom in ALPHABET if atom not in (b"\r", b"\n", b"\t")]
FUZZ_SEEDS = range(16)
FUZZ_STEPS = 40


def long_row(length: int, seed: str = "0123456789") -> str:
    return (seed * (length // len(seed) + 1))[:length]


def assert_retired(doc: LazyDocument, index: LongLineIndex) -> None:
    """The replaced index left the live ones; it is listed for joining only while its scan thread has not returned."""
    assert index not in doc._long.values()
    assert index in doc._retired or index.quiescent()


def three_rows(middle: str = "") -> str:
    return f"top\n{middle or long_row(700)}\nbottom"


class Spy:
    """Counts `LongLineIndex.spliced`, `start` and `_publish` calls (monkeypatched)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.spliced = 0
        self.starts = 0
        self.publishes = 0
        real_spliced = vars(LongLineIndex)["spliced"].__func__
        real_start = LongLineIndex.start
        real_publish = LongLineIndex._publish
        spy = self

        def spliced(cls: type[LongLineIndex], *args: object, **kwargs: object) -> LongLineIndex:
            spy.spliced += 1
            return real_spliced(cls, *args, **kwargs)

        def start(index: LongLineIndex) -> None:
            spy.starts += 1
            real_start(index)

        def publish(index: LongLineIndex, text: str, chars: int, disp: int, rel: int, *, polite: bool = True) -> tuple[int, int, int]:
            spy.publishes += 1
            return real_publish(index, text, chars, disp, rel, polite=polite)

        monkeypatch.setattr(LongLineIndex, "spliced", classmethod(spliced))
        monkeypatch.setattr(LongLineIndex, "start", start)
        monkeypatch.setattr(LongLineIndex, "_publish", publish)


# -- construction ------------------------------------------------------------------------------
def test_long_index_is_built_over_a_row_source() -> None:
    doc, _ = both(three_rows(), CONFIG)
    index = doc.long_index(1)
    assert isinstance(index._source, RowSource)
    assert index.start_offset == 0
    assert index.end_offset == 700
    assert index.join(10.0)
    assert index.total_chars == 700
    doc.close()


def test_long_index_after_an_edit_above_reads_the_edited_table() -> None:
    doc, ref = both(three_rows(), CONFIG)
    doc.replace_range((0, 0), (0, 0), "inserted\nlines\n")
    ref.replace_range((0, 0), (0, 0), "inserted\nlines\n")
    assert doc.long_index(3).join(10.0)
    assert_same(doc, ref)
    doc.close()


# -- re-keying -----------------------------------------------------------------------------------
def indexes_of(doc: LazyDocument) -> dict[int, LongLineIndex]:
    return dict(doc._long)


def test_edit_inside_a_long_row_splices_its_index_and_keeps_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [long_row(700, "ab"), "short", long_row(800, "cd"), long_row(900, "ef")]
    doc, ref = both("\n".join(rows), CONFIG)
    before = {row: doc.long_index(row) for row in (0, 2, 3)}
    settle(doc)
    spy = Spy(monkeypatch)
    doc.replace_range((2, 100), (2, 110), "XYZ")
    ref.replace_range((2, 100), (2, 110), "XYZ")
    after = indexes_of(doc)
    assert spy.spliced == 1
    assert after[0] is before[0]
    assert after[3] is before[3]
    assert after[2] is not before[2]
    assert not before[2].running
    assert_retired(doc, before[2])
    assert_same(doc, ref)
    doc.close()


def test_rows_below_keep_their_object_under_a_shifted_key(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, ref = both("short\n" + long_row(700) + "\n" + long_row(800, "xy"), CONFIG)
    below = doc.long_index(2)
    settle(doc)
    spy = Spy(monkeypatch)
    doc.replace_range((0, 2), (0, 2), "one\ntwo\n")  # two rows are added above
    ref.replace_range((0, 2), (0, 2), "one\ntwo\n")
    assert indexes_of(doc).get(4) is below
    assert spy.starts == 0
    assert doc.long_index(4) is below
    assert_same(doc, ref)
    doc.close()


def test_removing_rows_above_shifts_the_keys_down() -> None:
    doc, ref = both("a\nb\nc\n" + long_row(700), CONFIG)
    index = doc.long_index(3)
    settle(doc)
    doc.replace_range((0, 0), (2, 0), "")
    ref.replace_range((0, 0), (2, 0), "")
    assert indexes_of(doc).get(1) is index
    assert_same(doc, ref)
    doc.close()


def test_edit_in_a_row_above_a_long_row_keeps_the_object() -> None:
    doc, ref = both("head\n" + long_row(700), CONFIG)
    index = doc.long_index(1)
    settle(doc)
    doc.replace_range((0, 4), (0, 4), "er")
    ref.replace_range((0, 4), (0, 4), "er")
    assert doc.long_index(1) is index
    assert_same(doc, ref)
    doc.close()


def test_edit_adding_a_newline_retires_the_index_and_indexes_the_result_again(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, ref = both(three_rows(long_row(1100)), CONFIG)
    old = doc.long_index(1)
    settle(doc)
    spy = Spy(monkeypatch)
    doc.replace_range((1, 600), (1, 600), "\n")
    ref.replace_range((1, 600), (1, 600), "\n")
    assert spy.spliced == 0
    assert 1 not in indexes_of(doc)
    assert_retired(doc, old)
    assert not old.running
    assert doc.is_long(1)
    assert not doc.is_long(2)  # 500 characters are a medium row
    new = doc.long_index(1)
    assert new is not old
    assert new.join(10.0)
    assert new.total_chars == 600
    assert_same(doc, ref)
    doc.close()


def test_joining_two_rows_retires_both_and_indexes_the_long_row_from_scratch(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, ref = both(long_row(300) + "\n" + long_row(300) + "\n" + long_row(700, "zq"), CONFIG)
    last = doc.long_index(2)
    settle(doc)
    spy = Spy(monkeypatch)
    doc.replace_range((0, 300), (1, 0), "")
    ref.replace_range((0, 300), (1, 0), "")
    assert spy.spliced == 0
    assert doc.is_long(0)
    assert indexes_of(doc).get(1) is last
    first = doc.long_index(0)
    assert first.join(10.0)
    assert first.total_chars == 600
    assert_same(doc, ref)
    doc.close()


def test_edit_spanning_rows_retires_the_touched_indexes() -> None:
    doc, ref = both("\n".join([long_row(700, "a"), long_row(700, "b"), long_row(700, "c"), long_row(700, "d")]), CONFIG)
    held = {row: doc.long_index(row) for row in range(4)}
    settle(doc)
    doc.replace_range((1, 10), (2, 10), "mid")
    ref.replace_range((1, 10), (2, 10), "mid")
    after = indexes_of(doc)
    assert after.get(0) is held[0]
    assert after.get(2) is held[3]
    assert 1 not in after
    assert_retired(doc, held[1])
    assert_retired(doc, held[2])
    assert_same(doc, ref)
    doc.close()


def test_crlf_formed_by_a_deletion_shifts_the_rows_below_by_one() -> None:
    doc, ref = both("a\rb\n" + long_row(700), CONFIG)
    index = doc.long_index(2)
    settle(doc)
    doc.replace_range((1, 0), (1, 1), "")
    ref.replace_range((1, 0), (1, 1), "")
    assert ref.line_count == 2
    assert indexes_of(doc).get(1) is index
    assert_same(doc, ref)
    doc.close()


def test_row_shrinking_below_the_long_threshold_drops_its_index() -> None:
    doc, ref = both(three_rows(long_row(560)), CONFIG)
    index = doc.long_index(1)
    settle(doc)
    doc.replace_range((1, 0), (1, 100), "")
    ref.replace_range((1, 0), (1, 100), "")
    assert not doc.is_long(1)
    assert 1 not in indexes_of(doc)
    assert_retired(doc, index)
    assert_same(doc, ref)
    doc.close()


def test_medium_row_growing_into_a_long_row_gets_an_index() -> None:
    doc, ref = both(three_rows(long_row(400)), CONFIG)
    doc.replace_range((1, 10), (1, 10), long_row(300))
    ref.replace_range((1, 10), (1, 10), long_row(300))
    assert doc.is_long(1)
    assert doc.long_index(1).join(10.0)
    assert_same(doc, ref)
    doc.close()


def test_edit_larger_than_the_sync_limit_rebuilds_the_index(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, ref = both(three_rows(), CONFIG)
    old = doc.long_index(1)
    settle(doc)
    monkeypatch.setattr(_lazy_document, "SPLICE_SYNC_BYTES", 32)
    spy = Spy(monkeypatch)
    doc.replace_range((1, 50), (1, 60), long_row(100, "mn"))
    ref.replace_range((1, 50), (1, 60), long_row(100, "mn"))
    assert spy.spliced == 0
    assert_retired(doc, old)
    assert_same(doc, ref)
    doc.close()


def test_edit_in_a_long_row_whose_old_scan_is_unfinished_keeps_correct_answers() -> None:
    doc, ref = both(three_rows(long_row(3000)), replace(CONFIG, scan_block=64))
    doc.replace_range((1, 5), (1, 8), "QQ")
    ref.replace_range((1, 5), (1, 8), "QQ")
    for _ in range(3):
        doc.replace_range((1, 6), (1, 7), "R")
        ref.replace_range((1, 6), (1, 7), "R")
    assert_same(doc, ref)
    doc.close()


# -- no rescan -------------------------------------------------------------------------------------
@pytest.mark.parametrize("insert", ["QQ", "", "é" * 3, "x" * 5])
def test_columns_before_and_after_the_edit_are_available_at_once_without_a_rescan(monkeypatch: pytest.MonkeyPatch, insert: str) -> None:
    text = long_row(2000, "abcdefghij")
    doc, ref = both(three_rows(text), CONFIG)
    settle(doc)
    spy = Spy(monkeypatch)
    threads_before = threading.active_count()
    doc.replace_range((1, 500), (1, 510), insert)
    ref.replace_range((1, 500), (1, 510), insert)
    assert spy.publishes == 0
    assert spy.spliced == 1
    row = ref.rows[1]
    index = doc.long_index(1)
    assert index.frontier().complete
    assert doc.line_length(1) == len(row)
    for column in (0, 1, 63, 64, 499, 500, 500 + len(insert), 600, 1000, len(row) - 1, len(row)):
        expected = advance_disp(row[:column], 0, TAB)
        assert doc.display_column(1, column) == expected, column
        assert doc.byte_offset(1, column) == doc._range(1).start + utf8_len(row[:column])
        assert doc.column_slice(1, column, column + 5) == row[column : column + 5]
    assert doc.column_at_display(1, 700) == 700
    assert doc.has_char_at(1, len(row) - 1)
    assert not doc.has_char_at(1, len(row))
    assert threading.active_count() <= threads_before + 1
    doc.close()


def test_inserted_text_over_one_step_is_scanned_synchronously_without_a_rescan(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, ref = both(three_rows(long_row(2000)), CONFIG)
    settle(doc)
    spy = Spy(monkeypatch)
    block = long_row(300, "mnop")
    doc.replace_range((1, 500), (1, 500), block)
    ref.replace_range((1, 500), (1, 500), block)
    index = doc.long_index(1)
    assert index.frontier().complete
    assert doc.line_length(1) == 2300
    assert doc.display_column(1, 2200) == 2200
    assert spy.publishes <= (300 // 64) + 2  # the inserted text only, never the 2000 characters of the row
    assert_same(doc, ref)
    doc.close()


# -- merges of UTF-8 sequences in long rows (Task 6 limitation lifted) -------------------------------
def test_utf8_merge_in_a_long_row_is_applied_with_the_exact_end_location() -> None:
    text = long_row(300, "ab") + "\udce2" + long_row(400, "cd")
    doc, ref = both(three_rows(text), CONFIG)
    settle(doc)
    expected = ref.replace_range((1, 301), (1, 301), "\udc82\udcac")
    result = doc.replace_range((1, 301), (1, 301), "\udc82\udcac")
    assert result.end_location == expected == (1, 301)
    assert doc.is_long(1)
    assert_same(doc, ref)
    doc.close()


def test_utf8_merge_end_location_anchored_by_the_old_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_lazy_document, "RELOCATE_LIMIT", 16)
    text = long_row(300, "ab") + "\udce2" + long_row(400, "cd") + "\udc82" + long_row(100, "ef")
    doc, ref = both(three_rows(text), CONFIG)
    settle(doc)
    for start, end, inserted in [((1, 301), (1, 301), "\udc82\udcac"), ((1, 300), (1, 300), "\udce2"), ((1, 5), (1, 5), "é"), ((1, 303), (1, 303), "\udc82")]:
        expected = ref.replace_range(start, end, inserted)
        assert doc.replace_range(start, end, inserted).end_location == expected, (start, inserted)
        assert_same(doc, ref)
    doc.close()


def test_utf8_merge_by_deleting_the_middle_of_a_long_row() -> None:
    text = long_row(300, "ab") + "\udce2" + "XYZ" + "\udc82\udcac" + long_row(400, "cd")
    doc, ref = both(three_rows(text), CONFIG)
    settle(doc)
    expected = ref.replace_range((1, 301), (1, 304), "")
    assert doc.replace_range((1, 301), (1, 304), "").end_location == expected
    assert_same(doc, ref)
    doc.close()


def test_utf8_merge_with_a_long_end_row_after_a_multi_row_edit() -> None:
    text = "start\udce2\n" + long_row(300, "ab") + "\udc82\udcac" + long_row(400, "cd") + "\nend"
    doc, ref = both(text, CONFIG)
    settle(doc)
    expected = ref.replace_range((0, 6), (1, 0), "")
    assert doc.replace_range((0, 6), (1, 0), "").end_location == expected
    assert_same(doc, ref)
    doc.close()


# -- capability bounds ---------------------------------------------------------------------------------
def test_capability_calls_on_edited_long_rows_stay_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    config = LazyConfig(stride=4, index_long_line_threshold=64, long_row_threshold=512, word_wrap_limit=128, checkpoint_chars=1 << 20)
    step = index_step(config)
    assert step == MAX_WINDOW_CHARS
    text = long_row(60_000, "abcdefghij")
    doc, ref = both(three_rows(text), config)
    settle(doc)
    for start, end, inserted in [((1, 30_000), (1, 30_010), "é" * 20), ((1, 100), (1, 100), "x" * 12_000), ((1, 40_000), (1, 52_000), "")]:
        doc.replace_range(start, end, inserted)
        ref.replace_range(start, end, inserted)
    settle(doc)
    row = ref.rows[1]
    reads: list[tuple[int, bool]] = []
    real_read = RowSource.read

    def read(self: RowSource, offset: int, size: int, *, cache: bool = True) -> bytes:
        reads.append((size, cache))
        return real_read(self, offset, size, cache=cache)

    monkeypatch.setattr(RowSource, "read", read)
    doc.call_log.events.clear()
    for column in range(0, len(row), 1777):
        doc.column_slice(1, column, column + 30_000)
        doc.display_column(1, column)
        doc.column_at_display(1, advance_disp(row[:column], 0, TAB))
        doc.byte_offset(1, column)
        doc.has_char_at(1, column)
        doc.line_length(1)
    assert doc.call_log.events
    assert doc.call_log.max_decoded_chars <= MAX_WINDOW_CHARS
    assert max(size for size, _ in reads) <= 2 * MAX_WINDOW_CHARS * 4 + 8
    doc.close()


# -- tab limit (design 6.4) ----------------------------------------------------------------------------------
def test_tab_alignment_behind_an_edit_is_kept_from_before_the_edit() -> None:
    """Documented limit: behind an edit that changes the display width by other than a multiple of the tab width, columns can be off by less than a tab."""
    text = "a" * 10 + "\t" + long_row(700)
    doc, ref = both(three_rows(text), CONFIG)
    settle(doc)
    ref.replace_range((1, 5), (1, 5), "x")
    doc.replace_range((1, 5), (1, 5), "x")
    settle(doc)
    row = ref.rows[1]
    for column in (0, 4, 5, 6, 10):
        assert doc.display_column(1, column) == advance_disp(row[:column], 0, TAB)  # up to the edit: exact
    stale = doc.display_column(1, 300)
    exact = advance_disp(row[:300], 0, TAB)
    assert stale is not None
    assert stale != exact
    assert abs(stale - exact) < TAB
    reopened = LazyDocument.from_text(ref.text, CONFIG)
    settle(reopened)
    assert reopened.display_column(1, 300) == exact
    reopened.close()
    doc.close()


def test_a_width_change_by_a_multiple_of_the_tab_width_keeps_tabs_exact() -> None:
    text = "a" * 10 + "\t" + long_row(700)
    doc, ref = both(three_rows(text), CONFIG)
    settle(doc)
    ref.replace_range((1, 5), (1, 5), "xxxx")
    doc.replace_range((1, 5), (1, 5), "xxxx")
    settle(doc)
    row = ref.rows[1]
    for column in (3, 12, 64, 300, len(row)):
        assert doc.display_column(1, column) == advance_disp(row[:column], 0, TAB)
    doc.close()


# -- fuzz -----------------------------------------------------------------------------------------------------
def plain_text(rng: Rng, atoms: int) -> str:
    return decode(b"".join(rng.choice(PLAIN) for _ in range(atoms)))


def long_rows_text(rng: Rng) -> str:
    """Rows of mixed sizes, most of them long for CONFIG (more than 512 bytes), separated by random terminators."""
    parts: list[str] = []
    for _ in range(rng.randint(2, 5)):
        parts.append(plain_text(rng, rng.choice([5, 40, 300, 450, 700])))
        parts.append(rng.choice(["\n", "\n", "\r\n", "\r"]))
    parts.append(plain_text(rng, rng.randint(0, 400)))
    return "".join(parts)


def pick_long_row(rng: Rng, ref: RefText) -> int | None:
    candidates = [row for row, text in enumerate(ref.rows) if len(text.encode("utf-8", "surrogateescape")) > CONFIG.long_row_threshold]
    return rng.choice(candidates) if candidates else None


def check_immediate(doc: LazyDocument, ref: RefText, rng: Rng) -> None:
    """Whatever a long row answers before its scan is complete is exact; `None` and short slices are allowed."""
    for row in range(ref.line_count):
        if not doc.is_long(row):
            continue
        text = ref.rows[row]
        for _ in range(3):
            column = rng.randint(0, len(text))
            shown = doc.display_column(row, column)
            if shown is not None:
                assert shown == advance_disp(text[:column], 0, TAB), (row, column)
            offset = doc.byte_offset(row, column)
            if offset is not None:
                assert offset == doc._range(row).start + utf8_len(text[:column])
            piece = doc.column_slice(row, column, column + 7)
            assert text[column : column + 7].startswith(piece)


def check_long_capabilities(doc: LazyDocument, ref: RefText, rng: Rng) -> None:
    for row in range(ref.line_count):
        if not doc.is_long(row):
            continue
        text = ref.rows[row]
        assert doc.line_length(row) == len(text)
        for _ in range(6):
            column = rng.randint(0, len(text))
            disp = advance_disp(text[:column], 0, TAB)
            assert doc.display_column(row, column) == disp
            assert doc.byte_offset(row, column) == doc._range(row).start + utf8_len(text[:column])
            assert doc.has_char_at(row, column) == (column < len(text))
            covering = doc.column_at_display(row, disp)
            assert covering is not None
            assert advance_disp(text[:covering], 0, TAB) <= disp
            assert covering == column or advance_disp(text[: covering + 1], 0, TAB) > disp or text[column:column] == ""


def random_step(rng: Rng, ref: RefText) -> tuple[tuple[int, int], tuple[int, int], str]:
    kind = rng.randint(0, 9)
    row = pick_long_row(rng, ref)
    if row is None or kind == 9:
        row = rng.randint(0, ref.line_count - 1)
    text = ref.rows[row]
    column = rng.randint(0, len(text))
    start = (row, column)
    if kind <= 3:  # inside one row: insert, delete or replace
        end = (row, min(len(text), column + rng.choice([0, 0, 1, 3, 20, 150])))
        return start, end, plain_text(rng, rng.choice([0, 1, 3, 12, 40, 120]))
    if kind == 4:  # split a row
        return start, start, plain_text(rng, rng.randint(0, 4)) + "\n" + plain_text(rng, rng.randint(0, 4))
    if kind == 5 and row + 1 < ref.line_count:  # join two rows
        return (row, len(text)), (row + 1, 0), ""
    if kind == 6 and row + 1 < ref.line_count:  # replace across rows
        end = (min(row + rng.randint(1, 2), ref.line_count - 1), 0)
        return start, (end[0], rng.randint(0, len(ref.rows[end[0]]))), plain_text(rng, rng.randint(0, 30))
    if kind == 7:  # merge UTF-8 pieces at the edit
        return start, start, decode(rng.choice([b"\xe2", b"\x82\xac", b"\x82", b"\xac"]))
    return start, start, plain_text(rng, rng.randint(1, 60))


def run_long_fuzz(seed: int, steps: int) -> None:
    rng = Rng(seed)
    text = long_rows_text(rng)
    doc, ref = both(text, CONFIG)
    try:
        assert_same(doc, ref, rng)
        for step in range(steps):
            start, end, inserted = random_step(rng, ref)
            expected_end = ref.replace_range(start, end, inserted)
            result = doc.replace_range(start, end, inserted)
            assert result.end_location == expected_end, (seed, step, start, end, inserted)
            check_immediate(doc, ref, rng)
            assert_same(doc, ref, rng)
            check_long_capabilities(doc, ref, rng)
            if step % 7 == 0:
                for row in range(ref.line_count):
                    if doc.is_long(row):
                        assert doc.long_index(row).total_chars == len(ref.rows[row])
    finally:
        doc.close()
    assert doc.wait_closed(10.0)


@pytest.mark.parametrize("seed", FUZZ_SEEDS)
def test_fuzz_long_rows_against_the_str_reference(seed: int) -> None:
    run_long_fuzz(seed + 500, FUZZ_STEPS)


def test_fuzz_exercises_splices_and_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch)
    run_long_fuzz(900, FUZZ_STEPS)
    assert spy.spliced >= 3
    assert spy.starts > spy.spliced
