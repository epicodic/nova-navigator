"""The revision counter, the branch id and `modified` in `EditHistory` (ACT5 Task 13)."""

from __future__ import annotations

from nova_editor.document._history import EditHistory
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.document.test_edit_history import SMALL, area, new_history, perform, redo, undo


def typing_setup() -> tuple[LazyDocument, EditHistory]:
    return LazyDocument.from_text("abc", SMALL), new_history()


def type_char(doc: LazyDocument, history: EditHistory, char: str = "x") -> None:
    perform(area(doc), history, (0, 0), (0, 0), char)


def test_unmodified_after_open() -> None:
    _doc, history = typing_setup()
    assert not history.modified
    assert history.revision == 0


def test_modified_after_edit_and_undo_redo() -> None:
    doc, history = typing_setup()
    widget = area(doc)
    type_char(doc, history)
    assert history.modified
    assert undo(widget, history)
    assert not history.modified
    assert history.revision == 0
    assert redo(widget, history)
    assert history.modified
    assert history.revision == 1
    doc.close()


def test_mark_saved_and_typing_after_save() -> None:
    doc, history = typing_setup()
    widget = area(doc)
    type_char(doc, history)
    history.mark_saved()
    assert not history.modified
    type_char(doc, history, "y")
    assert history.modified
    assert undo(widget, history)
    assert not history.modified
    assert undo(widget, history)
    assert history.modified
    assert redo(widget, history)
    assert not history.modified
    doc.close()


def test_new_branch_cannot_reach_the_saved_state_by_revision() -> None:
    doc, history = typing_setup()
    widget = area(doc)
    type_char(doc, history, "x")
    history.checkpoint()
    type_char(doc, history, "y")
    history.mark_saved()
    saved_revision = history.revision
    assert undo(widget, history)
    type_char(doc, history, "z")
    assert history.revision == saved_revision
    assert history.modified
    assert undo(widget, history)
    assert history.modified
    assert redo(widget, history)
    assert history.modified
    doc.close()


def test_undo_to_the_saved_state_across_a_branch_point_is_unmodified() -> None:
    doc, history = typing_setup()
    widget = area(doc)
    type_char(doc, history, "x")
    history.mark_saved()
    type_char(doc, history, "y")
    assert undo(widget, history)
    type_char(doc, history, "z")
    assert undo(widget, history)
    assert not history.modified
    doc.close()


def test_checkpoint_keeps_coalesced_typing_from_spanning_a_save() -> None:
    doc, history = typing_setup()
    type_char(doc, history, "x")
    history.mark_saved()
    type_char(doc, history, "y")
    assert len(history.undo_stack) == 2
    doc.close()


def test_without_save_typing_coalesces() -> None:
    doc, history = typing_setup()
    for column in range(3):
        perform(area(doc), history, (0, column), (0, column), "x")
    assert len(history.undo_stack) == 1
    assert history.revision == 3
    doc.close()


def test_undo_and_redo_move_the_revision_with_the_batch() -> None:
    doc, history = typing_setup()
    widget = area(doc)
    for column in range(3):
        perform(area(doc), history, (0, column), (0, column), "x")
    batch = history.undo_stack[0]
    assert (batch.revision_before, batch.revision_after) == (0, 3)
    assert undo(widget, history)
    assert history.revision == 0
    assert redo(widget, history)
    assert history.revision == 3
    doc.close()


def test_restore_helpers_put_the_revision_back() -> None:
    doc, history = typing_setup()
    type_char(doc, history)
    batch = history._pop_undo()
    assert batch is not None
    assert history.revision == 0
    history._restore_undo(batch)
    assert history.revision == 1
    assert history.modified
    batch = history._pop_undo()
    assert batch is not None
    batch = history._pop_redo()
    assert batch is not None
    assert history.revision == 1
    history._restore_redo(batch)
    assert history.revision == 0
    doc.close()
