"""The cursor on a long row survives a save and rebase (ACT5 Task 12, DEC-26 note 2): the hermetic analogue of the 200 MB column case.

A 40 KiB single row with the long-row thresholds lowered stands in for `longline-200m.txt`; the real column 100,000,000 case is measured in Task 19.
The widget has no save API yet (Task 14), so the test drives the rebase on the document beneath the widget and then does what the widget will do after
a save: translate the history, call `refresh_after_rebase`.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nova_editor.document._cursor_anchor import CursorState
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import NovaTextArea
from tests.nova_editor.document.helpers_save import run_save
from tests.nova_editor.helpers_view import HostApp, await_first_layout, row_strip_text, wait_until

COLUMN = 25_000
ROW_CHARS = 40_000
JOIN_SECONDS = 20.0
TYPING_SECONDS = 0.05
CONFIG = LazyConfig(stride=4, index_long_line_threshold=64, long_row_threshold=512, word_wrap_limit=128, checkpoint_chars=64)
_UNIT = "abcdefgé"


def _row() -> str:
    return (_UNIT * (ROW_CHARS // len(_UNIT) + 1))[:ROW_CHARS]


def _doc(area: NovaTextArea) -> LazyDocument:
    doc = area.document
    assert isinstance(doc, LazyDocument)
    return doc


def _rebase(area: NovaTextArea, target: Path) -> None:
    """Save the document and rebase it, translate the history contents, and refresh the widget as `save` will."""
    doc = _doc(area)
    edits = [edit for batch in [*area.history.undo_stack, *area.history.redo_stack] for edit in batch]
    contents = [content for edit in edits for content in edit.contents()]
    _, plan = run_save(doc, target, contents)
    assert not plan.clear_history
    translated = iter(plan.contents)
    for edit in edits:
        edit.rewrite(translated)
    assert next(translated, None) is None
    assert doc.wait_rebased(JOIN_SECONDS)
    area.refresh_after_rebase()


@pytest.mark.asyncio
async def test_the_cursor_stays_resolved_at_its_column_after_a_save(tmp_path: Path) -> None:
    path = tmp_path / "row.txt"
    path.write_text(_row(), encoding="utf-8")
    area = NovaTextArea.open(path, config=CONFIG)
    async with HostApp(area).run_test(size=(60, 14)) as pilot:
        await await_first_layout(pilot, area)
        doc = _doc(area)
        assert doc.wait_indexed(JOIN_SECONDS)
        assert doc.long_index(0).join(JOIN_SECONDS)
        area.move_cursor((0, COLUMN))
        await wait_until(pilot, lambda: area.cursor_state is CursorState.RESOLVED)
        area.insert("Z")
        assert doc.long_index(0).join(JOIN_SECONDS)
        await wait_until(pilot, lambda: area.cursor_state is CursorState.RESOLVED)
        assert area.cursor_location == (0, COLUMN + 1)
        before_text = row_strip_text(area, 0)
        before_bytes = doc.read_bytes(0, doc.length)
        old_source = doc._source
        assert area._long_cursor.index is not None

        _rebase(area, path)

        assert doc._source is not old_source
        assert doc.read_bytes(0, doc.length) == before_bytes
        assert path.read_bytes() == before_bytes
        assert area._long_cursor.index is not None
        assert area._long_cursor.index.index is doc.long_index(0)
        assert area.cursor_state is CursorState.RESOLVED
        assert area.cursor_location == (0, COLUMN + 1)
        assert row_strip_text(area, 0) == before_text

        start = time.perf_counter()
        result = area._replace_via_keyboard("x", area.cursor_location, area.cursor_location)
        elapsed = time.perf_counter() - start
        assert result is not None
        assert elapsed < TYPING_SECONDS
        await wait_until(pilot, lambda: area.cursor_state is CursorState.RESOLVED)
        assert area.cursor_location == (0, COLUMN + 2)
        expected = _row()
        expected = expected[:COLUMN] + "Zx" + expected[COLUMN:]
        assert doc.read_bytes(0, doc.length).decode("utf-8") == expected

        area.undo()
        area.undo()
        assert doc.read_bytes(0, doc.length).decode("utf-8") == _row()
        area.redo()
        area.redo()
        assert doc.read_bytes(0, doc.length).decode("utf-8") == expected
        assert doc.long_index(0).join(JOIN_SECONDS)
        await wait_until(pilot, lambda: area.cursor_state is CursorState.RESOLVED)
        assert area.cursor_location == (0, COLUMN + 2)
