"""The shared undo/redo loop, rollback, reference API and `set_max_checkpoints` of `EditHistory` (ACT2 slice 2)."""

from __future__ import annotations

import dataclasses
from typing import Literal, cast

import pytest

from nova_editor.core.pieces import Content, PieceSource, make_unknown_piece
from nova_editor.document._byte_edit import ByteEdit
from nova_editor.document._document import EditResult
from nova_editor.document._edit import Edit
from nova_editor.document._errors import EditsLocked, RowUnavailable
from nova_editor.document._history import EditHistory, HistoryRolledBack, HistoryStep
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.document.test_byte_edit import EMPTY, hex_edit
from tests.nova_editor.document.test_edit_history import area, doc_bytes, new_history, perform
from tests.nova_editor.document.test_lazy_byte_edit import DATA, Stepped
from tests.nova_editor.document.test_lazy_edit import SMALL


def make_doc(data: bytes = b"hello world") -> LazyDocument:
    return LazyDocument.from_bytes(data, SMALL)


def record(history: EditHistory, doc: LazyDocument, start: int, end: int, data: bytes) -> ByteEdit:
    entry = hex_edit(doc, start, end, data)
    history.record(entry)
    return entry


def test_errors_are_still_importable_from_the_lazy_document() -> None:
    from nova_editor.document import _errors
    from nova_editor.document import _lazy_document as lazy

    assert lazy.RowUnavailable is _errors.RowUnavailable
    assert lazy.EditsLocked is _errors.EditsLocked
    assert issubclass(EditsLocked, RowUnavailable)


def test_undo_restores_bytes_and_redo_reapplies() -> None:
    doc = make_doc()
    history = new_history()
    states = [doc_bytes(doc)]
    entries: list[ByteEdit] = []
    for start, data in ((0, b"A"), (5, b"BB"), (9, b"C")):
        entries.append(record(history, doc, start, start, data))
        history.checkpoint()
        states.append(doc_bytes(doc))
    for index in (2, 1, 0):
        step = history.undo(doc)
        assert step is not None
        assert step.direction == "undo"
        assert step.byte == entries[index].start_byte
        assert doc_bytes(doc) == states[index]
    for index in (0, 1, 2):
        step = history.redo(doc)
        assert step is not None
        assert step.direction == "redo"
        entry = entries[index]
        assert entry.start_byte is not None
        assert entry.inserted is not None
        assert step.byte == entry.start_byte + entry.inserted.length
        assert doc_bytes(doc) == states[index + 1]
    doc.close()


def test_empty_stacks_return_none() -> None:
    doc = make_doc()
    history = new_history()
    assert history.undo(doc) is None
    assert history.redo(doc) is None
    doc.close()


def test_peek_accessors() -> None:
    doc = make_doc()
    history = new_history()
    assert history.peek_undo() is None
    assert history.peek_redo() is None
    record(history, doc, 0, 0, b"A")
    history.checkpoint()
    record(history, doc, 3, 3, b"B")
    top = history.undo_stack[-1]
    assert history.peek_undo() is top
    assert history.peek_redo() is None
    history.undo(doc)
    assert history.peek_redo() is top
    assert history.peek_undo() is history.undo_stack[-1]
    assert history.peek_undo() is not top
    doc.close()


def test_batch_with_two_entries_applies_last_to_first() -> None:
    doc = make_doc()
    history = new_history()
    first = record(history, doc, 0, 0, b"x")
    second = record(history, doc, 4, 4, b"y")
    assert [len(batch) for batch in history.undo_stack] == [2]
    step = history.undo(doc)
    assert step is not None
    assert step.applied == [second, first]
    assert len(step.results) == 2
    assert step.low <= step.high_old
    assert doc_bytes(doc) == b"hello world"
    step = history.redo(doc)
    assert step is not None
    assert step.applied == [first, second]
    assert len(step.results) == 2
    assert step.low <= step.high_new
    assert doc_bytes(doc) == b"xhelylo world"
    doc.close()


def test_text_then_hex_then_two_undos_restore_the_original() -> None:
    doc = LazyDocument.from_text("abc\ndef", SMALL)
    original = doc_bytes(doc)
    widget = area(doc)
    history = new_history()
    perform(widget, history, (0, 1), (0, 1), "XY")
    record(history, doc, 0, 0, b"!")
    assert history.modified
    typed = doc_bytes(doc)

    def mover(entry: ByteEdit, direction: Literal["undo", "redo"]) -> EditResult:
        if isinstance(entry, Edit):
            return entry.undo(widget) if direction == "undo" else entry.do(widget, record_selection=False)
        return entry.revert(doc) if direction == "undo" else entry.apply(doc)

    assert history.undo(doc, mover) is not None
    assert history.undo(doc, mover) is not None
    assert doc_bytes(doc) == original
    assert not history.modified
    assert history.redo(doc, mover) is not None
    assert history.redo(doc, mover) is not None
    assert doc_bytes(doc) == typed
    assert history.modified
    doc.close()


def test_default_mover_uses_apply_and_revert_for_byte_edits() -> None:
    doc = make_doc()
    history = new_history()
    calls: list[str] = []

    class Spy(ByteEdit):
        def apply(self, document: LazyDocument) -> EditResult:
            calls.append("apply")
            return super().apply(document)

        def revert(self, document: LazyDocument) -> EditResult:
            calls.append("revert")
            return super().revert(document)

    plain = hex_edit(doc, 0, 0, b"Z")
    assert plain.removed is not None
    assert plain.inserted is not None
    history.record(Spy(0, plain.removed, plain.inserted, origin="hex"))
    history.undo(doc)
    history.redo(doc)
    assert calls == ["revert", "apply"]
    doc.close()


@pytest.mark.parametrize("direction", ["undo", "redo"])
def test_locked_batch_rolls_back(direction: str, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = make_doc()
    history = new_history()
    record(history, doc, 0, 0, b"x")
    record(history, doc, 4, 4, b"y")
    if direction == "redo":
        history.undo(doc)
    before_bytes = doc_bytes(doc)
    before = (history.revision, history.branch, history.modified)
    stacks = (history.undo_stack, history.redo_stack)
    real = doc.splice_bytes
    counter = [0]

    def splice_bytes(start: int, end: int, content: Content) -> EditResult:
        counter[0] += 1
        if counter[0] == 2:
            raise EditsLocked("save")
        return real(start, end, content)

    monkeypatch.setattr(doc, "splice_bytes", splice_bytes)
    run = history.undo if direction == "undo" else history.redo
    with pytest.raises(RowUnavailable):
        run(doc)
    assert doc_bytes(doc) == before_bytes
    assert (history.revision, history.branch, history.modified) == before
    assert history.undo_stack == stacks[0]
    assert history.redo_stack == stacks[1]
    assert all(a is b for a, b in zip(history.undo_stack, stacks[0], strict=True))
    doc.close()


def test_failed_rollback_raises_history_rolled_back(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = make_doc()
    history = new_history()
    record(history, doc, 0, 0, b"x")
    record(history, doc, 4, 4, b"y")
    real = doc.splice_bytes
    counter = [0]

    def splice_bytes(start: int, end: int, content: Content) -> EditResult:
        counter[0] += 1
        if counter[0] in (2, 3):
            raise RowUnavailable("gone")
        return real(start, end, content)

    monkeypatch.setattr(doc, "splice_bytes", splice_bytes)
    with pytest.raises(HistoryRolledBack):
        history.undo(doc)
    doc.close()


def seeded_history() -> tuple[LazyDocument, EditHistory]:
    """An `Edit` and a hex entry in the undo stack, another hex entry in the redo stack."""
    doc = LazyDocument.from_text("abc\ndef", SMALL)
    history = new_history()
    perform(area(doc), history, (0, 1), (1, 1), "ZZ")
    record(history, doc, 0, 0, b"1")
    history.checkpoint()
    record(history, doc, 1, 1, b"2")
    history.undo(doc)
    return doc, history


def test_references_order_matches_rewrite_order() -> None:
    doc, history = seeded_history()
    expected = [content for batch in (*history.undo_stack, *history.redo_stack) for entry in batch for content in entry.contents()]
    assert len(expected) >= 4 + 2 + 2
    references = history.references()
    assert len(references) == len(expected)
    assert all(a is b for a, b in zip(references, expected, strict=True))
    doc.close()


def test_rewrite_references_consumes_exactly_len() -> None:
    doc, history = seeded_history()
    count = len(history.references())
    replacement = [dataclasses.replace(content) for content in history.references()]
    sentinel = Content.from_pieces((), 0)
    iterator = iter([*replacement, sentinel])
    history.rewrite_references(iterator)
    assert next(iterator) is sentinel
    assert all(a is b for a, b in zip(history.references(), replacement, strict=True))
    before = history.references()
    with pytest.raises(ValueError, match="too few contents"):
        history.rewrite_references(iter(replacement[: count - 1]))
    assert all(a is b for a, b in zip(history.references(), before, strict=True))
    doc.close()


def test_map_references_identity_and_all_or_nothing() -> None:
    doc, history = seeded_history()
    before = history.references()
    history.map_references(lambda content: content)
    assert all(a is b for a, b in zip(history.references(), before, strict=True))
    history.map_references(dataclasses.replace)
    after = history.references()
    assert after == before
    assert all(a is not b for a, b in zip(after, before, strict=True))
    calls = [0]

    def explode(content: Content) -> Content:
        calls[0] += 1
        if calls[0] == 3:
            raise RuntimeError("boom")
        return dataclasses.replace(content)

    with pytest.raises(RuntimeError):
        history.map_references(explode)
    assert all(a is b for a, b in zip(history.references(), after, strict=True))
    doc.close()


def test_original_ranges_report_original_pieces_only() -> None:
    doc = make_doc(b"abcdef")
    history = new_history()
    record(history, doc, 1, 3, b"")
    history.checkpoint()
    record(history, doc, 0, 0, b"zz")
    history.checkpoint()
    unknown = Content.from_pieces((make_unknown_piece(cast("PieceSource", doc._table._source), 0, 0, 3),), 0)
    history.record(ByteEdit(0, EMPTY, unknown, origin="hex"))
    assert sorted(history.original_ranges()) == [(0, 3), (1, 3)]
    doc.close()


def test_set_max_checkpoints_keeps_the_newest() -> None:
    doc = make_doc()
    history = new_history()
    for index in range(5):
        record(history, doc, index, index, b"x")
        history.checkpoint()
    batches = history.undo_stack
    history.set_max_checkpoints(2)
    assert history.max_checkpoints == 2
    assert all(a is b for a, b in zip(history.undo_stack, batches[-2:], strict=True))
    assert len(history.undo_stack) == 2
    history.set_max_checkpoints(None)
    for index in range(3):
        record(history, doc, index, index, b"y")
        history.checkpoint()
    assert len(history.undo_stack) == 5
    history.clear()
    assert history.undo_stack == []
    doc.close()


def test_blind_undo_redo_on_an_unscanned_document() -> None:
    case = Stepped(DATA)
    try:
        history = new_history()
        far = len(DATA) - 3
        assert case.doc.snapshot().scanned_bytes < far
        entry = hex_edit(case.doc, far, far, b"QQ")
        history.record(entry)
        edited = case.content()
        assert history.undo(case.doc) is not None
        assert case.content() == case.ref
        assert history.redo(case.doc) is not None
        assert case.content() == edited
    finally:
        case.source.stop()
        case.doc.close()


def test_history_step_is_exported() -> None:
    assert dataclasses.is_dataclass(HistoryStep)
