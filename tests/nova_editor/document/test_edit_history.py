"""`Edit` and `EditHistory` over pieces: undo and redo at byte level, coalescing, and the stock fallback (ACT4 Task 9)."""

from __future__ import annotations

from typing import cast

import pytest

from nova_editor.core.pieces import Content
from nova_editor.document._document import Document, Location, Selection
from nova_editor.document._edit import Edit
from nova_editor.document._history import EditHistory
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget._text_area import NovaTextArea
from tests.nova_editor.core.reference import Rng
from tests.nova_editor.document.reference_text import RefText
from tests.nova_editor.document.test_lazy_edit import LONG_ROWS, SMALL, assert_same, random_location, random_text

BIG = 10**9


class FakeArea:
    """The part of the text area that `Edit` touches."""

    def __init__(self, document: LazyDocument | Document) -> None:
        self.document = document
        self.selection = Selection.cursor((0, 0))
        self.cursor_width_records = 0

    def record_cursor_width(self) -> None:
        self.cursor_width_records += 1


def area(document: LazyDocument | Document) -> NovaTextArea:
    return cast("NovaTextArea", FakeArea(document))


def fake(widget: NovaTextArea) -> FakeArea:
    return cast("FakeArea", widget)


def new_history(*, timer: float = 1e9, chars: int = BIG) -> EditHistory:
    return EditHistory(checkpoint_timer=timer, checkpoint_max_characters=chars)


def perform(widget: NovaTextArea, history: EditHistory, start: Location, end: Location, text: str) -> Edit:
    """Do an edit and record it, as `NovaTextArea.edit` does."""
    edit = Edit(text, start, end, maintain_selection_offset=False)
    edit.do(widget)
    history.record(edit)
    edit.after(widget)
    return edit


def undo(widget: NovaTextArea, history: EditHistory) -> bool:
    batch = history._pop_undo()
    if not batch:
        return False
    for edit in reversed(batch):
        edit.undo(widget)
    for edit in reversed(batch):
        edit.after(widget)
    return True


def redo(widget: NovaTextArea, history: EditHistory) -> bool:
    batch = history._pop_redo()
    if not batch:
        return False
    for edit in batch:
        edit.do(widget, record_selection=False)
    for edit in batch:
        edit.after(widget)
    return True


def doc_bytes(doc: LazyDocument) -> bytes:
    return doc._table.read(0, doc.length)


# -- unit tests --------------------------------------------------------------------------------
def test_do_records_bytes_and_pieces() -> None:
    doc = LazyDocument.from_text("hello\nworld", SMALL)
    widget = area(doc)
    edit = Edit("XY", (0, 1), (1, 2), maintain_selection_offset=False)
    result = edit.do(widget)
    assert edit.start_byte == 1
    assert edit.removed is not None
    assert edit.removed.length == len("ello\nwo")
    assert edit.inserted is not None
    assert edit.inserted.length == 2
    assert edit.end_location == result.end_location == (0, 3)
    assert doc.read_all(100) == "hXYrld"
    doc.close()


def test_undo_and_redo_restore_text_and_selection() -> None:
    doc = LazyDocument.from_text("abc\ndef", SMALL)
    widget = area(doc)
    fake(widget).selection = Selection((0, 1), (1, 1))
    history = new_history()
    perform(widget, history, (0, 1), (1, 1), "ZZ")
    assert doc.read_all(100) == "aZZef"
    assert fake(widget).selection == Selection.cursor((0, 3))
    assert undo(widget, history)
    assert doc.read_all(100) == "abc\ndef"
    assert fake(widget).selection == Selection((0, 1), (1, 1))
    assert redo(widget, history)
    assert doc.read_all(100) == "aZZef"
    assert fake(widget).cursor_width_records >= 3
    doc.close()


def test_stock_document_keeps_the_text_based_path() -> None:
    doc = Document("abc\ndef")
    widget = area(doc)
    history = new_history()
    edit = perform(widget, history, (0, 1), (1, 1), "Z")
    assert edit.start_byte is None
    assert doc.text == "aZef"
    assert undo(widget, history)
    assert doc.text == "abc\ndef"
    assert redo(widget, history)
    assert doc.text == "aZef"


def test_default_history_is_unlimited() -> None:
    history = EditHistory()
    assert history.max_checkpoints is None
    assert history._undo_stack.maxlen is None


def test_typing_of_a_thousand_characters_is_one_record() -> None:
    doc = LazyDocument.from_text("", SMALL)
    widget = area(doc)
    history = new_history()
    for column in range(1000):
        perform(widget, history, (0, column), (0, column), "x" if column % 3 else "é")
    typed = doc.read_all(10_000)
    assert len(typed) == 1000
    assert len(history.undo_stack) == 1
    assert len(history.undo_stack[0]) == 1
    only = history.undo_stack[0][0]
    assert only.inserted is not None
    assert only.inserted.length == len(typed.encode())
    assert len(only.inserted.pieces) == 1
    assert only.end_location == (0, 1000)
    assert undo(widget, history)
    assert doc.read_all(100) == ""
    assert redo(widget, history)
    assert doc.read_all(10_000) == typed
    assert fake(widget).selection == Selection.cursor((0, 1000))
    doc.close()


def test_typing_restores_the_selection_from_before_the_first_character() -> None:
    doc = LazyDocument.from_text("ab", SMALL)
    widget = area(doc)
    fake(widget).selection = Selection.cursor((0, 1))
    history = new_history()
    for column, char in enumerate("xyz", start=1):
        perform(widget, history, (0, column), (0, column), char)
    assert undo(widget, history)
    assert fake(widget).selection == Selection.cursor((0, 1))
    assert doc.read_all(10) == "ab"
    doc.close()


def test_typing_with_a_gap_does_not_coalesce() -> None:
    doc = LazyDocument.from_text("abcdef", SMALL)
    widget = area(doc)
    history = new_history()
    perform(widget, history, (0, 1), (0, 1), "x")
    perform(widget, history, (0, 5), (0, 5), "y")
    assert [len(batch) for batch in history.undo_stack] == [2]
    assert doc.read_all(100) == "axbcdyef"
    assert undo(widget, history)
    assert doc.read_all(100) == "abcdef"
    doc.close()


def test_backspace_run_coalesces() -> None:
    doc = LazyDocument.from_text("abcdefgh", SMALL)
    widget = area(doc)
    history = new_history()
    for column in range(8, 3, -1):
        perform(widget, history, (0, column - 1), (0, column), "")
    assert doc.read_all(100) == "abc"
    assert [len(batch) for batch in history.undo_stack] == [1]
    edit = history.undo_stack[0][0]
    assert edit.removed is not None
    assert edit.removed.length == 5
    assert edit.start_byte == 3
    assert undo(widget, history)
    assert doc.read_all(100) == "abcdefgh"
    assert redo(widget, history)
    assert doc.read_all(100) == "abc"
    doc.close()


def test_delete_run_coalesces() -> None:
    doc = LazyDocument.from_text("abcdefgh", SMALL)
    widget = area(doc)
    history = new_history()
    for _ in range(4):
        perform(widget, history, (0, 2), (0, 3), "")
    assert doc.read_all(100) == "abgh"
    assert [len(batch) for batch in history.undo_stack] == [1]
    assert undo(widget, history)
    assert doc.read_all(100) == "abcdefgh"
    assert redo(widget, history)
    assert doc.read_all(100) == "abgh"
    doc.close()


def test_insertions_and_deletions_stay_separate_batches() -> None:
    doc = LazyDocument.from_text("abc", SMALL)
    widget = area(doc)
    history = new_history()
    perform(widget, history, (0, 3), (0, 3), "d")
    perform(widget, history, (0, 3), (0, 4), "")
    assert len(history.undo_stack) == 2
    doc.close()


def test_newline_paste_and_checkpoint_rules_are_kept() -> None:
    doc = LazyDocument.from_text("", SMALL)
    widget = area(doc)
    history = new_history()
    perform(widget, history, (0, 0), (0, 0), "a")
    perform(widget, history, (0, 1), (0, 1), "b")
    perform(widget, history, (0, 2), (0, 2), "\n")
    perform(widget, history, (1, 0), (1, 0), "c")
    perform(widget, history, (1, 1), (1, 1), "pasted")
    history.checkpoint()
    perform(widget, history, (1, 7), (1, 7), "d")
    assert [len(batch) for batch in history.undo_stack] == [1, 1, 1, 1, 1]
    while undo(widget, history):
        pass
    assert doc.read_all(100) == ""
    while redo(widget, history):
        pass
    assert doc.read_all(100) == "ab\ncpastedd"
    doc.close()


def test_character_limit_forms_a_new_batch() -> None:
    doc = LazyDocument.from_text("", SMALL)
    widget = area(doc)
    history = new_history(chars=3)
    for column in range(7):
        perform(widget, history, (0, column), (0, column), "x")
    assert [len(batch) for batch in history.undo_stack] == [1, 1, 1]
    assert doc.length == 7
    doc.close()


def test_typed_invalid_bytes_do_not_coalesce_and_restore_exactly() -> None:
    data = b"ab\xe2\x82\xac\xff\r\ncd\xe2\x82\n"
    doc = LazyDocument.from_bytes(data, SMALL)
    widget = area(doc)
    history = new_history()
    perform(widget, history, (0, 2), (0, 2), "\udce2")
    perform(widget, history, (0, 3), (0, 3), "\udc82")
    perform(widget, history, (0, 4), (0, 4), "\udcac")
    after = doc_bytes(doc)
    assert after.startswith(b"ab\xe2\x82\xac")
    while undo(widget, history):
        pass
    assert doc_bytes(doc) == data
    while redo(widget, history):
        pass
    assert doc_bytes(doc) == after
    doc.close()


def test_crlf_restores_exactly_after_a_deletion_that_joined_cr_and_lf() -> None:
    data = b"a\rXY\nb\r\nc"
    doc = LazyDocument.from_bytes(data, SMALL)
    ref = RefText(data.decode())
    widget = area(doc)
    history = new_history()
    perform(widget, history, (1, 0), (1, 2), "")
    ref.replace_range((1, 0), (1, 2), "")
    assert doc_bytes(doc) == b"a\r\nb\r\nc"
    assert doc.line_count == 3
    assert undo(widget, history)
    assert doc_bytes(doc) == data
    assert doc.line_count == 4
    assert [doc.get_line(row) for row in range(4)] == ["a", "XY", "b", "c"]
    assert redo(widget, history)
    assert doc_bytes(doc) == b"a\r\nb\r\nc"
    assert doc.line_count == 3
    assert_same(doc, ref)
    doc.close()


def test_crlf_terminators_survive_edit_undo_redo() -> None:
    data = b"one\r\ntwo\r\nthree"
    doc = LazyDocument.from_bytes(data, SMALL)
    widget = area(doc)
    history = new_history()
    perform(widget, history, (0, 1), (2, 2), "\n")
    changed = doc_bytes(doc)
    assert undo(widget, history)
    assert doc_bytes(doc) == data
    assert redo(widget, history)
    assert doc_bytes(doc) == changed
    doc.close()


def test_splice_bytes_validates_the_range() -> None:
    doc = LazyDocument.from_text("abc", SMALL)
    empty = Content.from_pieces([], 0)
    with pytest.raises(ValueError, match="invalid byte range"):
        doc.splice_bytes(2, 1, empty)
    with pytest.raises(ValueError, match="beyond the document"):
        doc.splice_bytes(0, 9, empty)
    doc.close()


# -- fuzz --------------------------------------------------------------------------------------
def random_edit(rng: Rng, ref: RefText, insert: int) -> tuple[Location, Location, str]:
    start = random_location(rng, ref)
    end = random_location(rng, ref) if rng.randint(0, 2) else start
    if rng.randint(0, 3) == 0:
        end = (min(start[0] + rng.randint(0, 1), ref.line_count - 1), start[1])
        end = (end[0], min(end[1], len(ref.row(end[0]))))
    text = random_text(rng, insert) if rng.randint(0, 4) else ""
    return start, end, text


def run_prefix_fuzz(seed: int, config: LazyConfig, steps: int, *, initial: int, insert: int) -> None:
    rng = Rng(seed)
    doc = LazyDocument.from_text(random_text(rng, initial), config)
    ref = RefText(doc.read_all(10**9))
    widget = area(doc)
    history = new_history()
    states = [(ref.data(), ref.text)]
    try:
        for _ in range(steps):
            start, end, text = random_edit(rng, ref, insert)
            trial = RefText(ref.text)
            trial.replace_range(start, end, text)
            edit = perform(widget, history, start, end, text)
            history.checkpoint()
            assert edit.removed is not None
            assert edit.inserted is not None
            if edit.removed.length == 0 and edit.inserted.length == 0:
                continue  # the history does not record an edit that changed nothing
            ref.text = trial.text
            states.append((ref.data(), ref.text))
        steps = len(states) - 1
        assert (doc_bytes(doc), doc.read_all(10**9)) == states[-1]
        for index in range(steps - 1, -1, -1):
            assert undo(widget, history), seed
            assert (doc_bytes(doc), doc.read_all(10**9)) == states[index], (seed, index)
        assert not undo(widget, history)
        for index in range(1, steps + 1):
            assert redo(widget, history), seed
            assert (doc_bytes(doc), doc.read_all(10**9)) == states[index], (seed, index)
        for index in range(steps - 1, steps // 2, -1):
            assert undo(widget, history)
            assert doc_bytes(doc) == states[index][0]
        for index in range(steps // 2 + 2, steps + 1):
            assert redo(widget, history)
            assert doc_bytes(doc) == states[index][0]
        assert_same(doc, RefText(states[-1][1]))
    finally:
        doc.close()


@pytest.mark.parametrize("seed", range(24))
def test_undo_redo_of_every_prefix_matches_the_reference(seed: int) -> None:
    run_prefix_fuzz(seed, SMALL, 40, initial=30, insert=8)


@pytest.mark.parametrize("seed", range(6))
def test_undo_redo_of_every_prefix_with_long_rows(seed: int) -> None:
    run_prefix_fuzz(seed + 200, LONG_ROWS, 20, initial=60, insert=14)


@pytest.mark.parametrize("seed", range(12))
def test_coalesced_batches_undo_to_earlier_states(seed: int) -> None:
    """Typing and deleting runs without checkpoints: every undo lands on a state the document had, and the ends match."""
    rng = Rng(seed + 300)
    doc = LazyDocument.from_text(random_text(rng, 40), SMALL)
    ref = RefText(doc.read_all(10**9))
    widget = area(doc)
    history = new_history()
    states = [ref.data()]
    row, column = 0, 0
    try:
        for _ in range(60):
            mode = rng.randint(0, 3)
            row = min(row, ref.line_count - 1)
            column = min(column, len(ref.row(row)))
            if mode == 0 and column > 0:
                start, end, text = (row, column - 1), (row, column), ""
                column -= 1
            elif mode == 1 and column < len(ref.row(row)):
                start, end, text = (row, column), (row, column + 1), ""
            elif mode == 2:
                start = end = (row, column)
                text = rng.choice(["a", "b", "é", "\udcff", "x"])
                column += 1
            else:
                start, end, text = random_edit(rng, ref, 5)
                row, column = start
                history.checkpoint()
            trial = RefText(ref.text)
            trial.replace_range(start, end, text)
            perform(widget, history, start, end, text)
            ref.text = trial.text
            states.append(ref.data())
            assert doc_bytes(doc) == states[-1]
        final = states[-1]
        position = len(states) - 1
        while undo(widget, history):
            current = doc_bytes(doc)
            assert current in states[:position], seed
            position = max(i for i in range(position) if states[i] == current)
        assert doc_bytes(doc) == states[0]
        while redo(widget, history):
            pass
        assert doc_bytes(doc) == final
    finally:
        doc.close()


# -- very large deletion -----------------------------------------------------------------------
ROW = 32_768
ROWS = 32_768  # 1 GiB


class RowsSource:
    """A 1 GiB source of identical rows that generates its bytes on demand and counts what the UI path reads."""

    def __init__(self) -> None:
        self._row = b"a" * (ROW - 1) + b"\n"
        self.ui_bytes = 0

    def length(self) -> int:
        return ROW * ROWS

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        size = max(0, min(size, ROW * ROWS - offset))
        data = (self._row * (size // ROW + 2))[offset % ROW : offset % ROW + size]
        if cache:
            self.ui_bytes += len(data)
        return data

    def close(self) -> None:
        pass


def test_gigabyte_deletion_undoes_and_redoes_without_reading_data() -> None:
    source = RowsSource()
    doc = LazyDocument(source, LazyConfig(stride=4096, scan_block=1 << 24, sync_scan_limit=0), autostart=False)
    doc.start_scan()
    assert doc.wait_indexed(120.0)
    assert doc.line_count == ROWS + 1
    widget = area(doc)
    history = new_history()
    source.ui_bytes = 0
    edit = perform(widget, history, (100, 10), (30_000, 20), "")
    gigantic = (30_000 - 100) * ROW + 10
    assert edit.removed is not None
    assert edit.removed.length == gigantic
    assert len(edit.removed.pieces) <= 3
    assert doc.length == ROW * ROWS - gigantic
    assert undo(widget, history)
    assert doc.length == ROW * ROWS
    assert doc._table.tree.piece_count <= 3
    assert redo(widget, history)
    assert doc.length == ROW * ROWS - gigantic
    assert undo(widget, history)
    assert doc._table.tree.piece_count <= 3
    assert doc.line_count == ROWS + 1
    assert source.ui_bytes < 8 * ROW
    doc.close()
