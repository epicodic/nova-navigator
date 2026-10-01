"""`LazyDocument.replace_range` and `splice` against the `str` reference model (ACT4 Task 6)."""

from __future__ import annotations

import pytest
from textual._tree_sitter import get_language

from nova_editor.core import BytesSource, Content
from nova_editor.document._document import EditResult
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument, RowUnavailable
from nova_editor.document._syntax_aware_document import SyntaxAwareDocument
from tests.nova_editor.core.reference import ALPHABET, Rng
from tests.nova_editor.document.reference_text import RefText, decode

SMALL = LazyConfig(stride=4, index_long_line_threshold=64, long_row_threshold=512, word_wrap_limit=128, checkpoint_chars=64)
LONG_ROWS = LazyConfig(stride=4, index_long_line_threshold=8, long_row_threshold=16, word_wrap_limit=8, checkpoint_chars=8)
FUZZ_SEEDS = range(24)
FUZZ_STEPS = 60


def open_doc(text: str, config: LazyConfig = SMALL) -> LazyDocument:
    return LazyDocument.from_text(text, config)


def settle(doc: LazyDocument) -> None:
    """Wait until every long row of `doc` has a complete long index (long rows are scanned in the background)."""
    for row in range(doc.line_count):
        if doc.is_long(row):
            assert doc.long_index(row).join(10.0)


def row_text(doc: LazyDocument, row: int) -> str:
    """The characters of a row, short or long (long rows through windows)."""
    if not doc.is_long(row):
        return doc.get_line(row)
    total = doc.line_length(row)
    assert total is not None
    return "".join(doc.column_slice(row, a, min(a + 4, total)) for a in range(0, total, 4))


def assert_same(doc: LazyDocument, ref: RefText, rng: Rng | None = None) -> None:
    """Compare every observable of `doc` with the reference."""
    settle(doc)
    assert doc.line_count == ref.line_count
    assert doc.length == len(ref.data())
    assert doc.newline == ref.newline
    for row, expected in enumerate(ref.rows):
        assert row_text(doc, row) == expected, f"row {row}"
        assert doc.row_byte_length(row) == len(expected.encode("utf-8", "surrogateescape"))
    assert doc.end == ref.end
    assert doc.read_all(10**9) == ref.text
    if doc.line_count <= 128 and not any(doc.is_long(row) for row in range(doc.line_count)):
        assert doc.get_text_range((0, 0), ref.end) == ref.get_text_range((0, 0), ref.end)
    if rng is not None:
        for _ in range(4):
            a = (rng.randint(0, ref.line_count - 1), 0)
            b = (rng.randint(0, ref.line_count - 1), 0)
            a = (a[0], rng.randint(0, len(ref.rows[a[0]])))
            b = (b[0], rng.randint(0, len(ref.rows[b[0]])))
            if any(doc.is_long(row) for row in {a[0], b[0], *range(min(a[0], b[0]), max(a[0], b[0]) + 1)}):
                continue
            assert doc.get_text_range(a, b) == ref.get_text_range(a, b)
    assert doc._table.read(0, doc.length) == ref.data()


def edit(doc: LazyDocument, ref: RefText, start: tuple[int, int], end: tuple[int, int], text: str) -> EditResult:
    """Apply the same edit to both and compare the end location."""
    expected_end = ref.replace_range(start, end, text)
    result = doc.replace_range(start, end, text)
    assert result.end_location == expected_end
    assert_same(doc, ref)
    return result


def both(text: str, config: LazyConfig = SMALL) -> tuple[LazyDocument, RefText]:
    return open_doc(text, config), RefText(text)


# -- unit tests --------------------------------------------------------------------------------
def test_insert_in_single_row() -> None:
    doc, ref = both("hello\nworld")
    result = edit(doc, ref, (0, 2), (0, 2), "XY")
    assert doc.get_line(0) == "heXYllo"
    assert result.end_location == (0, 4)
    assert result.replaced_text == ""
    assert result.removed is not None
    assert result.removed.length == 0
    doc.close()


def test_delete_in_single_row_reports_removed_text_and_content() -> None:
    doc, ref = both("hello\nworld")
    result = edit(doc, ref, (0, 1), (0, 4), "")
    assert result.replaced_text == "ell"
    assert result.removed is not None
    assert doc._table.content_bytes(result.removed, 0, result.removed.length) == b"ell"
    assert result.end_location == (0, 1)
    doc.close()


def test_multi_row_delete() -> None:
    doc, ref = both("one\ntwo\nthree\nfour")
    result = edit(doc, ref, (0, 2), (2, 3), "")
    assert doc.get_line(0) == "onee"
    assert result.end_location == (0, 2)
    assert result.replaced_text == "e\ntwo\nthr"
    assert result.removed is not None
    assert result.removed.breaks == 2
    doc.close()


def test_replace_across_rows() -> None:
    doc, ref = both("one\ntwo\nthree")
    edit(doc, ref, (0, 1), (2, 2), "A\nB\nC")
    assert doc.line_count == 3
    doc.close()


def test_insert_with_newlines_end_location() -> None:
    doc, ref = both("abc\ndef")
    result = edit(doc, ref, (0, 1), (0, 1), "x\ny\nz")
    assert result.end_location == (2, 1)
    assert doc.line_count == 4
    doc.close()


def test_trailing_newline_insert() -> None:
    doc, ref = both("abc")
    result = edit(doc, ref, (0, 3), (0, 3), "\n")
    assert result.end_location == (1, 0)
    doc.close()


def test_start_and_end_of_document() -> None:
    doc, ref = both("middle")
    edit(doc, ref, (0, 0), (0, 0), "start ")
    edit(doc, ref, ref.end, ref.end, " end")
    edit(doc, ref, (0, 0), ref.end, "")
    assert doc.line_count == 1
    assert doc.length == 0
    doc.close()


def test_empty_document() -> None:
    doc, ref = both("")
    edit(doc, ref, (0, 0), (0, 0), "a\nb")
    edit(doc, ref, (0, 0), (1, 1), "")
    edit(doc, ref, (0, 0), (0, 0), "")
    doc.close()


def test_bom_row_is_an_ordinary_character() -> None:
    doc, ref = both("﻿abc\nx")
    edit(doc, ref, (0, 0), (0, 1), "")
    edit(doc, ref, (0, 0), (0, 0), "﻿")
    doc.close()


@pytest.mark.parametrize("text", ["a\r\nb\r\nc", "a\rb\rc", "a\r\nb\nc\rd\r\n", "\r\n\r\n", "a\n\r\nb"], ids=repr)
def test_terminator_styles_and_mixed_endings(text: str) -> None:
    doc, ref = both(text)
    for row in range(ref.line_count):
        edit(doc, ref, (row, 0), (row, 0), "ins\nert")
    edit(doc, ref, (0, 1), (ref.line_count - 1, 0), "mid\nmid")
    edit(doc, ref, (1, 0), (1, 0), "\n\n")
    doc.close()


def test_inserted_newlines_follow_row_terminator() -> None:
    doc, ref = both("a\r\nb\nc")
    edit(doc, ref, (0, 1), (0, 1), "x\ny\rz")
    assert doc.read_all(100) == "ax\r\ny\r\nz\r\nb\nc"
    edit(doc, ref, (1, 1), (1, 1), "p\nq")
    assert doc.read_all(100) == "ax\r\nyp\r\nq\r\nz\r\nb\nc"
    edit(doc, ref, (4, 0), (4, 0), "\r\n")
    assert doc.read_all(100).endswith("z\r\n\nb\nc")
    doc.close()


def test_last_row_without_terminator_uses_document_newline() -> None:
    doc, ref = both("a\r\nb")
    edit(doc, ref, (1, 1), (1, 1), "\nc")
    assert doc.read_all(100) == "a\r\nb\r\nc"
    doc.close()


def test_invalid_bytes_edits() -> None:
    doc, ref = both("a\udcff\udce2b\nc\udc80")
    edit(doc, ref, (0, 1), (0, 2), "")
    edit(doc, ref, (0, 1), (0, 1), "\udcff")
    edit(doc, ref, (1, 1), (1, 2), "z")
    assert doc._table.read(0, doc.length) == b"a\xff\xe2b\ncz"
    doc.close()


def test_newline_correction_cr_on_the_left() -> None:
    """Insertion at the start of a row after a CR: T must not start with LF, so the LF row terminator is replaced by CRLF."""
    doc, ref = both("a\rb\n")
    result = edit(doc, ref, (1, 0), (1, 0), "x\ny")
    assert result.end_location == (2, 1)
    assert doc._table.read(0, doc.length) == b"a\rx\r\nyb\n"
    assert doc.line_count == 4
    doc.close()


def test_newline_correction_lf_on_the_right() -> None:
    """The row terminator of the start row is CR, but an LF follows the range: T must not end with CR, so LF is used."""
    doc, ref = both("a\rb\nc")
    result = edit(doc, ref, (0, 1), (1, 1), "x\ny")
    assert result.end_location == (1, 1)
    assert doc._table.read(0, doc.length) == b"ax\ny\nc"
    doc.close()


def test_newline_correction_cr_left_and_lf_right_gives_crlf() -> None:
    doc, ref = both("a\rb\nc")
    result = edit(doc, ref, (1, 0), (1, 1), "p\nq")
    assert result.end_location == (2, 1)
    assert doc._table.read(0, doc.length) == b"a\rp\r\nq\nc"
    assert doc.line_count == 4
    doc.close()


def test_deletion_joining_cr_and_lf_reduces_row_count() -> None:
    doc, ref = both("a\rb\nc")
    assert doc.line_count == 3
    result = edit(doc, ref, (1, 0), (1, 1), "")
    assert result.end_location == (1, 0)
    assert doc.line_count == 2
    assert doc.get_line(1) == "c"
    doc.close()


def test_merge_guard_inserted_continuation_bytes() -> None:
    """A truncated lead byte on the left and inserted continuation bytes make one character."""
    doc, ref = both("a\udce2z")
    result = edit(doc, ref, (0, 2), (0, 2), "\udc82\udcac")
    assert doc.get_line(0) == "a€z"
    assert result.end_location == (0, 2)
    doc.close()


def test_merge_guard_inserted_lead_byte_and_right_continuations() -> None:
    doc, ref = both("a\udc82\udcacz")
    result = edit(doc, ref, (0, 1), (0, 1), "\udce2")
    assert doc.get_line(0) == "a\u20acz"
    assert result.end_location == (0, 2)
    doc.close()


def test_merge_guard_deletion_joins_the_halves() -> None:
    doc, ref = both("a\udce2XY\udc82\udcacz\nnext")
    result = edit(doc, ref, (0, 2), (0, 4), "")
    assert doc.get_line(0) == "a€z"
    assert result.end_location == (0, 2)
    assert doc.get_line(1) == "next"
    doc.close()


def test_merge_guard_three_way_junction() -> None:
    doc, ref = both("a\udce2\udcacz")
    edit(doc, ref, (0, 2), (0, 2), "\udc82")
    assert doc.get_line(0) == "a€z"
    doc.close()


def test_merge_guard_later_rows_stay_correct() -> None:
    doc, ref = both("a\udce2\nb\nc\udc82\udcac")
    edit(doc, ref, (0, 2), (0, 2), "\udc82\udcac")
    edit(doc, ref, (2, 0), (2, 0), "\udce2")
    doc.close()


def test_edit_on_closed_document_raises() -> None:
    doc = open_doc("abc")
    doc.close()
    with pytest.raises(RowUnavailable):
        doc.replace_range((0, 0), (0, 1), "x")
    with pytest.raises(RowUnavailable):
        doc.splice((0, 0), (0, 1), Content.from_pieces([], 0))
    assert doc.wait_closed(10.0)


def test_swapped_locations_are_sorted() -> None:
    doc, ref = both("abc\ndef")
    edit(doc, ref, (1, 2), (0, 1), "-")
    doc.close()


def test_subscribers_are_called_after_an_edit() -> None:
    doc = open_doc("abc")
    calls: list[int] = []
    doc.subscribe(lambda: calls.append(1))
    doc.replace_range((0, 0), (0, 0), "x")
    assert calls
    doc.close()


def test_newline_property_follows_edits_of_the_first_rows() -> None:
    doc, ref = both("a\nb")
    assert doc.newline == "\n"
    edit(doc, ref, (0, 1), (1, 0), "\r\n")
    assert doc.read_all(100) == "a\nb"
    doc.close()
    doc, ref = both("a\r\nb\nc")
    assert doc.newline == "\r\n"
    edit(doc, ref, (0, 1), (1, 0), "")
    assert doc.newline == "\n"
    edit(doc, ref, (0, 0), (0, 0), "x\r")
    assert doc.newline == "\n"
    doc.close()


def test_mirror_receives_the_same_edit() -> None:
    doc = open_doc("one\ntwo\nthree")
    language = get_language("python")
    if language is None:
        pytest.skip("tree-sitter python grammar not installed")
    mirror = SyntaxAwareDocument("one\ntwo\nthree", language)
    doc.attach_syntax(mirror)
    doc.replace_range((0, 1), (1, 1), "X\nY")
    assert mirror.text == "oX\nYwo\nthree"
    assert mirror.text == doc.read_all(100)
    doc.close()


def test_splice_inserts_content_and_computes_the_end_location() -> None:
    doc, ref = both("abc\ndef\nghi")
    removed = doc.replace_range((0, 1), (1, 2), "").removed
    assert removed is not None
    ref.replace_range((0, 1), (1, 2), "")
    result = doc.splice((0, 1), (0, 1), removed)
    assert result.end_location == (1, 2)
    assert result.removed is not None
    assert result.removed.length == 0
    assert doc.read_all(100) == "abc\ndef\nghi"
    doc.close()


def test_splice_replaced_text_limit() -> None:
    doc = open_doc("x" * 70_000 + "\nrest")
    result = doc.replace_range((0, 0), (0, 70_000), "")
    assert result.replaced_text == ""
    assert result.removed is not None
    assert result.removed.length == 70_000
    result = doc.replace_range((0, 0), (0, 3), "y")
    assert result.replaced_text == ""
    doc.close()
    doc = open_doc("abcdef")
    assert doc.replace_range((0, 0), (0, 3), "").replaced_text == "abc"
    doc.close()


def test_typing_extends_the_previous_add_piece() -> None:
    doc = open_doc("")
    for column, char in enumerate("hello"):
        doc.replace_range((0, column), (0, column), char)
    assert doc._table.tree.piece_count == 1
    assert doc.get_line(0) == "hello"
    doc.close()


def test_negative_row_is_an_index_error() -> None:
    doc = open_doc("a\nb\nc")
    with pytest.raises(IndexError):
        doc.replace_range((-1, 0), (0, 0), "x")
    doc.close()


def test_edit_at_a_row_that_is_not_scanned_yet_raises_row_unavailable() -> None:
    doc = LazyDocument(BytesSource(b"a\nb\nc"), SMALL, autostart=False)
    with pytest.raises(RowUnavailable):
        doc.replace_range((0, 0), (0, 0), "x")
    assert doc.length == 5
    doc.close()


def test_edit_before_a_long_row_keeps_the_row_readable() -> None:
    long_text = "L" * 700
    doc, ref = both(f"top\n{long_text}\nbottom")
    settle(doc)
    edit(doc, ref, (0, 1), (0, 1), "ZZ")
    edit(doc, ref, (0, 0), (0, 0), "a\nb\n")
    assert doc.is_long(ref.line_count - 2)
    edit(doc, ref, (ref.line_count - 1, 0), (ref.line_count - 1, 0), "!")
    doc.close()


def test_edit_inside_a_long_row() -> None:
    doc, ref = both("top\n" + "0123456789" * 70 + "\nbottom")
    settle(doc)
    edit(doc, ref, (1, 100), (1, 100), "abc")
    edit(doc, ref, (1, 5), (1, 650), "")
    edit(doc, ref, (1, 0), (1, 0), "x" * 600)
    assert doc.is_long(1)
    doc.close()


def test_edit_in_a_long_row_before_the_index_is_scanned_is_refused_not_wrong() -> None:
    doc = open_doc("top\n" + "0123456789" * 70 + "\nbottom")
    try:
        doc.replace_range((1, 600), (1, 600), "x")
    except RowUnavailable:
        pass  # allowed: the column was not resolved yet
    else:
        settle(doc)
        assert doc.line_length(1) == 701
    doc.close()


# -- fuzz --------------------------------------------------------------------------------------
def random_text(rng: Rng, limit: int) -> str:
    return decode(b"".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, limit))))


def random_location(rng: Rng, ref: RefText) -> tuple[int, int]:
    row = rng.randint(0, ref.line_count - 1)
    return (row, rng.randint(0, len(ref.row(row))))


def run_fuzz(seed: int, config: LazyConfig, steps: int, *, initial: int, insert: int) -> None:
    rng = Rng(seed)
    text = random_text(rng, initial)
    doc, ref = both(text, config)
    try:
        assert_same(doc, ref, rng)
        for _ in range(steps):
            start = random_location(rng, ref)
            end = random_location(rng, ref) if rng.randint(0, 2) else start
            if rng.randint(0, 3) == 0:
                end = (min(start[0] + rng.randint(0, 1), ref.line_count - 1), start[1])
                end = (end[0], min(end[1], len(ref.row(end[0]))))
            inserted = random_text(rng, insert) if rng.randint(0, 4) else ""
            trial = RefText(ref.text)
            expected_end = trial.replace_range(start, end, inserted)
            try:
                result = doc.replace_range(start, end, inserted)
            except RowUnavailable:
                # Only an edit that merges UTF-8 characters inside a long row is refused (until ACT4 Task 8); it must leave the document unchanged.
                assert config is LONG_ROWS
                assert_same(doc, ref, rng)
                continue
            ref.text = trial.text
            assert result.end_location == expected_end, (seed, start, end, inserted)
            assert_same(doc, ref, rng)
    finally:
        doc.close()


@pytest.mark.parametrize("seed", FUZZ_SEEDS)
def test_fuzz_against_the_str_reference(seed: int) -> None:
    run_fuzz(seed, SMALL, FUZZ_STEPS, initial=30, insert=8)


@pytest.mark.parametrize("seed", range(8))
def test_fuzz_with_long_rows(seed: int) -> None:
    run_fuzz(seed + 100, LONG_ROWS, 25, initial=60, insert=14)
