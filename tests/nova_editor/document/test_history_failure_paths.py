"""Undo and redo are all-or-nothing for any failure, and a refused redo is a full restore (bounded undo stack)."""

from __future__ import annotations

import pytest

from nova_editor.core.pieces import Content
from nova_editor.document._document import EditResult
from nova_editor.document._errors import RowUnavailable
from nova_editor.document._history import EditHistory
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.document.test_edit_history import doc_bytes, new_history
from tests.nova_editor.document.test_shared_history import make_doc, record


def two_entry_batch() -> tuple[LazyDocument, EditHistory]:
    doc = make_doc()
    history = new_history()
    record(history, doc, 0, 0, b"x")
    record(history, doc, 4, 4, b"y")
    assert len(history.undo_stack) == 1
    assert len(history.undo_stack[0]) == 2
    return doc, history


def fail_second_call(doc: LazyDocument, monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    real = doc.splice_bytes
    counter = [0]

    def splice_bytes(start: int, end: int, content: Content) -> EditResult:
        counter[0] += 1
        if counter[0] == 2:
            raise error
        return real(start, end, content)

    monkeypatch.setattr(doc, "splice_bytes", splice_bytes)


def snapshot(doc: LazyDocument, history: EditHistory) -> tuple[bytes, list[object], list[object], int, int, bool]:
    return (
        doc_bytes(doc),
        list(history.undo_stack),
        list(history.redo_stack),
        history.revision,
        history.branch,
        history.modified,
    )


def test_value_error_mid_undo_is_rolled_back(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, history = two_entry_batch()
    before = snapshot(doc, history)
    fail_second_call(doc, monkeypatch, ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        history.undo(doc)
    assert snapshot(doc, history) == before
    monkeypatch.undo()
    assert history.undo(doc) is not None
    assert doc_bytes(doc) == b"hello world"
    doc.close()


def test_value_error_mid_redo_is_rolled_back(monkeypatch: pytest.MonkeyPatch) -> None:
    doc, history = two_entry_batch()
    assert history.undo(doc) is not None
    before = snapshot(doc, history)
    fail_second_call(doc, monkeypatch, ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        history.redo(doc)
    assert snapshot(doc, history) == before
    monkeypatch.undo()
    assert history.redo(doc) is not None
    doc.close()


def test_refused_redo_keeps_every_batch_of_a_full_undo_stack() -> None:
    doc = make_doc()
    history = EditHistory(max_checkpoints=3, checkpoint_timer=1e9, checkpoint_max_characters=10_000)
    for start, data in ((0, b"A"), (3, b"B"), (6, b"C")):
        record(history, doc, start, start, data)
        history.checkpoint()
    assert history.undo(doc) is not None
    history.set_max_checkpoints(2)
    assert len(history.undo_stack) == 2
    history._force_end_batch = False
    before = snapshot(doc, history)

    def refuse(*_args: object) -> EditResult:
        raise RowUnavailable("gone")

    with pytest.raises(RowUnavailable):
        history.redo(doc, refuse)
    assert snapshot(doc, history) == before
    assert history._force_end_batch is False
    doc.close()


def test_redo_with_a_zero_checkpoint_limit_does_not_raise() -> None:
    doc = make_doc()
    history = new_history()
    record(history, doc, 0, 0, b"x")
    assert history.undo(doc) is not None
    history.set_max_checkpoints(0)
    assert history.redo(doc) is not None
    assert history.undo_stack == []
    assert history.redo_stack == []
    assert history._redo_evicted is None
    doc.close()


def test_refused_redo_with_a_zero_checkpoint_limit_restores_everything() -> None:
    doc = make_doc()
    history = new_history()
    record(history, doc, 0, 0, b"x")
    assert history.undo(doc) is not None
    history.set_max_checkpoints(0)
    before = snapshot(doc, history)

    def refuse(*_args: object) -> EditResult:
        raise RowUnavailable("gone")

    with pytest.raises(RowUnavailable):
        history.redo(doc, refuse)
    assert snapshot(doc, history) == before
    assert len(history.redo_stack) == 1
    doc.close()


def test_successful_redo_clears_the_remembered_eviction() -> None:
    doc = make_doc()
    history = EditHistory(max_checkpoints=2, checkpoint_timer=1e9, checkpoint_max_characters=10_000)
    for start, data in ((0, b"A"), (3, b"B"), (6, b"C")):
        record(history, doc, start, start, data)
        history.checkpoint()
    assert history.undo(doc) is not None
    history.set_max_checkpoints(1)
    history._force_end_batch = True
    assert history.redo(doc) is not None
    assert history._redo_evicted is None
    assert history._redo_flag_before is False
    doc.close()
