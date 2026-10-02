"""Post-swap holder audit of `LazyDocument.apply_rebase` (ACT5 Task 12, DEC-26 note 2).

The audit enumerated every attribute or local that stores a `LineIndex`, `LongLineIndex`, `PreadSource`, `AddStore`, `PieceTable`, `OriginalSource` or
`RowSource` outside the table (`rg -n "LongLineIndex|_line_index|_source\\b|row_source|AddStore|subscribe" src/nova_editor`):

| Holder | Owner | What happens at the swap |
|---|---|---|
| `_source`, `_line_index`, `_add_store`, `_table` | `LazyDocument` | replaced by `_install` |
| `_legacy_files` | `LazyDocument` | carried (kept) or released (`clear_history`) by `_install` |
| `_ranges`, `_texts` (row range and text caches) | `LazyDocument` | cleared by `_install` |
| `_long` (long indexes over `RowSource` of the old table) | `LazyDocument` | rebased onto the new table, or dropped and rebuilt on demand |
| `_retired` (cancelled long indexes), `_reapers` | `LazyDocument` | the retired list moves to the closer thread, which joins it and the reapers |
| `_subscribers` | `LazyDocument` | subscribed to the new line index and to every rebased long index; the old indexes drop theirs |
| `LineIndex._subscribers` (old index) | old `LineIndex` | cleared when the old index is released |
| `LongLineIndex._subscribers` (old indexes) | old `LongLineIndex` | cleared when the index is retired |
| `_newline` | `LazyDocument` | the content is identical after a save, so the cached value stays valid (not a holder of an object) |
| `CallLog` (`call_log`) | `LazyDocument` | holds counters and row numbers only, no object of the table |
| `LongRowAnchorIndex._index` (`LongRowCursor.index`, `CursorMachine._index`) | widget `LongRowCursor` | `rebind` re-points the adapter at the document's current long index (`LongRowCursor.rebase`) |
| `LazyWrappedDocument._blocks`, `_disp_cache`, `_short_rows` | wrapped document | keyed by row and valid for identical text; they hold numbers and strings only, never an index |
| `LazyWrappedDocument._lazy` | wrapped document | the document itself, not the table, so it needs no re-pointing |
| `_lazy_window` row cache | widget | none exists: `window_text` and `section_window` are pure functions over the document |
| widget `_line_cache` | widget | strips of rendered rows (text only); identical text renders identical strips |
| `Edit`, `EditResult`, clipboard record `Content` | history | translated by `prepare_rebase`/`Edit.rewrite` (Tasks 11 and 14), not by this audit |

Tests (a) to (d) below cover subscribers, long indexes, reachability of closed objects and the wrapped document; the widget case is in
`tests/nova_editor/test_save_long_row_cursor.py`.
"""

from __future__ import annotations

import gc
import types
from pathlib import Path

from nova_editor.core import LineIndex, PreadSource
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.document._lazy_wrapped_document import LazyWrappedDocument
from tests.nova_editor.document.helpers_save import JOIN_SECONDS, Session, open_pread_document

LONG_ROWS = (1, 3, 4)
WRAP_WIDTH = 24
TAB_WIDTH = 4
DEPTH = 6
CONFIG = LazyConfig(stride=4, index_long_line_threshold=64, long_row_threshold=512, word_wrap_limit=128, checkpoint_chars=64, max_long_indexes=8)
_SKIPPED = (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.CodeType, types.FrameType)


def _data() -> bytes:
    rows = [
        b"short",
        ("abc\té日" * 300).encode(),
        b"middle row " * 12,
        ("0123456789 " * 150).encode(),
        ("é" * 900).encode(),
        b"tail",
    ]
    return b"\n".join(rows)


def _long_answers(doc: LazyDocument) -> dict[int, tuple[int | None, int | None]]:
    """`(total_chars, try_char_to_byte(1000))` of every long row."""
    answers = {}
    for row in LONG_ROWS:
        index = doc.long_index(row)
        assert index.join(JOIN_SECONDS)
        answers[row] = (index.total_chars, index.try_char_to_byte(1000))
    return answers


def _reachable(root: object, depth: int) -> list[object]:
    """Every object reachable from `root` through at most `depth` referent steps, without crossing classes, modules or functions."""
    seen = {id(root)}
    found: list[object] = []
    level = [root]
    for _ in range(depth):
        following: list[object] = []
        for item in level:
            for child in gc.get_referents(item):
                if id(child) in seen or isinstance(child, _SKIPPED):
                    continue
                seen.add(id(child))
                found.append(child)
                following.append(child)
        level = following
    return found


def _wrapped_view(wrapped: LazyWrappedDocument, doc: LazyDocument, row: int) -> tuple[object, ...]:
    """What the wrapped document answers about `row`: its sections (short and medium rows) or its section starts and x offsets (long rows)."""
    if doc.is_long(row):
        count = wrapped.row_sections(row)
        return (count, [wrapped.section_start(row, section) for section in range(count)], [wrapped.section_x(row, column) for column in (0, 500, 1000)])
    return (wrapped.get_sections(row), list(wrapped.get_offsets(row)))


def _save(tmp_path: Path) -> tuple[Session, object, object, LazyWrappedDocument, list[int]]:
    doc, original = open_pread_document(tmp_path, _data(), CONFIG)
    session = Session(doc, original, tmp_path)
    calls: list[int] = []
    doc.subscribe(lambda: calls.append(1))
    doc.wait_indexed(JOIN_SECONDS)
    wrapped = LazyWrappedDocument(doc, WRAP_WIDTH, TAB_WIDTH)
    return session, doc._source, doc._line_index, wrapped, calls


def test_the_subscriber_is_called_by_the_new_line_index_and_not_by_the_old(tmp_path: Path) -> None:
    session, _old_source, old_index, _wrapped, calls = _save(tmp_path)
    doc = session.doc
    try:
        _long_answers(doc)
        old_longs = list(doc._long.values())
        assert old_longs
        session.edit((0, 0), (0, 1), "S")
        calls.clear()
        session.save(tmp_path / "doc.bin")
        assert doc.wait_rebased(JOIN_SECONDS)
        assert isinstance(old_index, LineIndex)
        assert calls, "the new line index never called the subscriber"
        calls.clear()
        old_index._notify()
        assert not calls, "the old line index still calls the subscriber"
        for old in old_longs:
            old._notify()
        assert not calls, "an old long index still calls the subscriber"
        doc._line_index._notify()
        assert calls, "the new line index does not call the subscriber"
        for index in doc._long.values():
            calls.clear()
            index._notify()
            assert calls, "a rebased long index does not call the subscriber"
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)


def test_every_long_index_is_rebased_or_rebuilt_with_the_same_answers(tmp_path: Path) -> None:
    session, _old_source, _old_index, _wrapped, _calls = _save(tmp_path)
    doc = session.doc
    try:
        before = _long_answers(doc)
        old_longs = dict(doc._long)
        session.edit((0, 0), (0, 1), "S")
        session.save(tmp_path / "doc.bin")
        assert doc.wait_rebased(JOIN_SECONDS)
        for row in LONG_ROWS:
            index = doc.long_index(row)
            assert index is not old_longs[row]
            assert index.join(JOIN_SECONDS)
            assert (index.total_chars, index.try_char_to_byte(1000)) == before[row]
        session.check_saved(tmp_path / "doc.bin")
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)


def test_nothing_reachable_from_the_document_refers_to_a_closed_source_or_the_old_line_index(tmp_path: Path) -> None:
    session, old_source, old_index, wrapped, _calls = _save(tmp_path)
    doc = session.doc
    try:
        _long_answers(doc)
        _wrapped_view(wrapped, doc, 1)
        assert any(found is old_index for found in _reachable(doc, DEPTH)), "the walk cannot see the line index"
        session.edit((0, 0), (0, 1), "S")
        session.save(tmp_path / "doc.bin")
        assert doc.wait_rebased(JOIN_SECONDS)
        _long_answers(doc)
        assert isinstance(old_source, PreadSource)
        assert old_source._closed
        for root in (doc, wrapped):
            for found in _reachable(root, DEPTH):
                assert found is not old_source
                assert found is not old_index
                assert not (isinstance(found, PreadSource) and found._closed)
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)


def test_wrapped_rows_measured_before_the_save_wrap_the_same_after_it(tmp_path: Path) -> None:
    session, _old_source, _old_index, wrapped, _calls = _save(tmp_path)
    doc = session.doc
    try:
        _long_answers(doc)
        rows = range(doc.line_count)
        measured = {row: _wrapped_view(wrapped, doc, row) for row in rows}
        tops = {row: wrapped.y_of_row(row) for row in rows}
        session.edit((5, 0), (5, 1), "T")  # an edit below the long rows: their measurements stay valid, the edited row is measured again
        wrapped.wrap_range((5, 0), (5, 1), (5, 1))
        measured[5] = _wrapped_view(wrapped, doc, 5)
        session.save(tmp_path / "doc.bin")
        assert doc.wait_rebased(JOIN_SECONDS)
        _long_answers(doc)
        fresh = LazyWrappedDocument(doc, WRAP_WIDTH, TAB_WIDTH)
        for row in rows:
            assert _wrapped_view(wrapped, doc, row) == measured[row]
            assert _wrapped_view(fresh, doc, row) == measured[row]
            assert wrapped.y_of_row(row) == tops[row]
            assert fresh.y_of_row(row) == tops[row]
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)
