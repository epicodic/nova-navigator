"""`ByteEdit`, the view-neutral history entry, and the unknown-piece rule of `shape` and `coalesce` (ACT2 slice 1)."""

from __future__ import annotations

import subprocess
import sys

from nova_editor.core.pieces import Content, make_unknown_piece
from nova_editor.document._byte_edit import ByteEdit
from nova_editor.document._document import Selection
from nova_editor.document._edit import Edit
from nova_editor.document._history import EditHistory
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.document.test_edit_history import area, new_history, perform
from tests.nova_editor.document.test_lazy_edit import SMALL

EMPTY = Content.from_pieces((), 0)


def hex_edit(doc: LazyDocument, start: int, end: int, data: bytes) -> ByteEdit:
    """Apply a byte splice to `doc` and wrap it the way the hex view will."""
    content = doc._table.add(data) if data else EMPTY
    result = doc.splice_bytes(start, end, content)
    assert result.removed is not None
    assert result.inserted is not None
    return ByteEdit(start, result.removed, result.inserted, origin="hex")


def test_byte_edit_apply_and_revert_restore_the_bytes() -> None:
    doc = LazyDocument.from_bytes(b"hello world", SMALL)
    entry = hex_edit(doc, 0, 5, b"HOWDY")
    assert doc.read_bytes(0, doc.length) == b"HOWDY world"
    entry.revert(doc)
    assert doc.read_bytes(0, doc.length) == b"hello world"
    entry.apply(doc)
    assert doc.read_bytes(0, doc.length) == b"HOWDY world"
    doc.close()


def test_byte_edit_defaults_and_hints() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    entry = hex_edit(doc, 1, 1, b"XY")
    assert entry.origin == "hex"
    assert entry.characters == 2
    assert entry.cursor_before == 1
    assert entry.cursor_after == 3
    assert entry.coalesce(entry) is False
    doc.close()


def test_byte_edit_shape() -> None:
    doc = LazyDocument.from_bytes(b"abc\ndef", SMALL)
    insert = hex_edit(doc, 0, 0, b"x")
    delete_newline = hex_edit(doc, 4, 5, b"")
    noop = ByteEdit(0, EMPTY, EMPTY, origin="hex")
    assert noop.shape() is None
    assert insert.shape() == (False, False)  # (is_replacement, may_contain_newline)
    assert delete_newline.shape() == (True, True)
    doc.close()


def test_unknown_content_may_contain_a_newline() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    piece = make_unknown_piece(doc._table._source, 0, 0, 3)
    unknown = Content.from_pieces((piece,), 0)
    assert unknown.unknown > 0
    assert unknown.breaks == 0
    assert ByteEdit(0, EMPTY, unknown, origin="hex").shape() == (False, True)
    assert ByteEdit(0, unknown, EMPTY, origin="hex").shape() == (True, True)
    doc.close()


def test_history_ends_the_batch_after_an_unknown_edit() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    history = new_history()
    piece = make_unknown_piece(doc._table._source, 0, 0, 3)
    unknown = Content.from_pieces((piece,), 0)
    history.record(ByteEdit(0, EMPTY, unknown, origin="hex"))
    history.record(ByteEdit(3, EMPTY, hex_edit(doc, 0, 0, b"x").inserted, origin="hex"))
    assert len(history.undo_stack) == 2  # never merged, and a checkpoint followed the unknown edit
    doc.close()


def test_origin_change_starts_a_new_batch_even_inside_the_timer() -> None:
    doc = LazyDocument.from_text("abc", SMALL)
    history = new_history()
    widget = area(doc)
    perform(widget, history, (0, 0), (0, 0), "x")
    history.record(hex_edit(doc, 0, 0, b"y"))
    assert [len(batch) for batch in history.undo_stack] == [1, 1]
    doc.close()


def test_text_edit_refuses_a_foreign_later_entry() -> None:
    doc = LazyDocument.from_text("abc", SMALL)
    history = new_history()
    edit = perform(area(doc), history, (0, 0), (0, 0), "x")
    assert edit.origin == "text"
    assert edit.coalesce(hex_edit(doc, 1, 1, b"y")) is False
    doc.close()


def test_edit_positional_construction_still_works() -> None:
    edit = Edit("x", (0, 0), (0, 0), False)
    assert edit.start_byte is None
    assert edit.origin == "text"
    assert edit.insert_content is None
    assert Selection.cursor((0, 0)) is not None


def test_history_module_imports_without_the_widget_in_a_fresh_interpreter() -> None:
    code = "import nova_editor.document._history, sys; assert 'nova_editor.widget._text_area' not in sys.modules"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
    assert EditHistory is not None
