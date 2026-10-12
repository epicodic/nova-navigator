"""Undo, redo, grouping and the cross-view acceptance of hex edits (S0003 ACT5 commit 4, REQ-13)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nova_editor.app import NovaEditApp
from nova_editor.core import SourceChanged
from nova_editor.document._history import HistoryRolledBack
from nova_editor.widget import NovaTextArea
from nova_editor.widget.hex_view import HexCursor
from tests.nova_editor.save_widget_helpers import SETTINGS
from tests.nova_editor.test_editor_screen_hex import app_for, screen_of, toggle
from tests.nova_editor.test_shared_history_widget import hex_edit
from tests.nova_editor.widget.hex_helpers import visible_height
from tests.nova_editor.widget.test_hex_edit import session

WIDE = (160, 24)


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small save chunks and no poll during a test."""
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)
    monkeypatch.setattr(NovaEditApp, "POLL_SECONDS", 3600.0)


# -- grouping at the view level ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typed_digits_of_a_run_are_one_batch(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(8)) as s:
        await s.press("4", "1", "4", "2")
        assert s.data() == bytes([0x41, 0x42]) + bytes(6)
        assert len(s.document.history.undo_stack) == 1  # typing from commit 3 already groups


@pytest.mark.asyncio
async def test_one_undo_takes_back_the_whole_typed_run_and_redo_restores_it(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(8)) as s:
        await s.press("4", "1", "4", "2")
        await s.press("ctrl+z")
        assert s.data() == bytes(8)
        await s.press("ctrl+y")
        assert s.data() == bytes([0x41, 0x42]) + bytes(6)


@pytest.mark.asyncio
async def test_inserted_digits_undo_together(tmp_path: Path) -> None:
    async with session(tmp_path, b"\x01\x02") as s:
        await s.press("insert", "4", "1", "4", "2")
        assert s.data() == bytes([0x41, 0x42, 0x01, 0x02])
        await s.press("ctrl+z")
        assert s.data() == b"\x01\x02"


@pytest.mark.asyncio
async def test_typing_then_moving_then_typing_takes_two_undos(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(64)) as s:
        await s.press("4", "1", "down", "4", "2")
        assert s.data()[0] == 0x41
        assert s.data()[17] == 0x42  # "down" from byte 1 lands on byte 17
        await s.press("ctrl+z")
        assert s.data() == bytes([0x41]) + bytes(63)
        await s.press("ctrl+z")
        assert s.data() == bytes(64)


@pytest.mark.asyncio
async def test_a_mode_change_ends_the_batch(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(8)) as s:
        await s.press("4", "1", "insert", "4", "2")
        assert len(s.document.history.undo_stack) == 2
        await s.press("ctrl+z")
        assert s.data() == bytes([0x41]) + bytes(7)
        await s.press("ctrl+z")
        assert s.data() == bytes(8)


@pytest.mark.asyncio
async def test_a_delete_run_is_one_batch_and_typing_before_it_is_another(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcdef") as s:
        await s.press("delete", "delete", "delete")
        assert s.data() == b"def"
        await s.press("ctrl+z")
        assert s.data() == b"abcdef"


@pytest.mark.asyncio
async def test_a_selection_delete_is_its_own_step(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcdef") as s:
        await s.press("delete", "shift+right", "shift+right", "delete")
        assert s.data() == b"def"
        await s.press("ctrl+z")
        assert s.data() == b"bcdef"
        await s.press("ctrl+z")
        assert s.data() == b"abcdef"


# -- undo and redo ----------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_undo_and_redo_with_nothing_to_do_change_nothing(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(8)) as s:
        before = s.view.cursor
        await s.press("ctrl+z")
        await s.press("ctrl+y")
        assert s.data() == bytes(8)
        assert s.view.cursor == before
        s.bell.assert_not_called()
        assert s.host.edited == 0


@pytest.mark.asyncio
async def test_undo_puts_the_cursor_on_the_edit_and_redo_after_it(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(64)) as s:
        await s.press("down", "right", "right", "4")  # overwrite the high digit of byte 17
        assert s.data()[17] == 0x40
        await s.press("ctrl+home")
        await s.press("ctrl+z")
        assert s.view.cursor == HexCursor(17, 0, "hex")
        assert s.view.selection is None
        await s.press("ctrl+home")
        await s.press("ctrl+y")
        assert s.view.cursor == HexCursor(18, 0, "hex")


@pytest.mark.asyncio
async def test_undo_and_redo_post_edited_and_move_modified(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(8)) as s:
        await s.press("4")
        assert s.document.history.modified
        edited = s.host.edited
        await s.press("ctrl+z")
        assert s.host.edited == edited + 1
        assert not s.document.history.modified
        await s.press("ctrl+y")
        assert s.host.edited == edited + 2
        assert s.document.history.modified


@pytest.mark.asyncio
async def test_undo_scrolls_a_far_edit_into_view_and_redo_too(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(4000)) as s:
        s.view.goto(3000)
        await s.pilot.pause()
        await s.press("4")
        await s.press("ctrl+home")
        assert s.view.scroll_offset.y == 0
        await s.press("ctrl+z")
        row = s.view.cursor.offset // s.view.hex_layout.bytes_per_row
        assert s.view.cursor.offset == 3000
        assert s.view.scroll_offset.y <= row < s.view.scroll_offset.y + visible_height(s.view)
        await s.press("ctrl+home")
        await s.press("ctrl+y")
        row = s.view.cursor.offset // s.view.hex_layout.bytes_per_row
        assert s.view.cursor.offset == 3001
        assert s.view.scroll_offset.y <= row < s.view.scroll_offset.y + visible_height(s.view)


@pytest.mark.asyncio
async def test_undo_of_an_append_lands_on_the_end_position(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(4)) as s:
        await s.press("ctrl+end", "4")
        assert s.view.length == 5
        await s.press("ctrl+z")
        assert s.view.length == 4
        assert s.view.cursor == HexCursor(4, 0, "hex")


@pytest.mark.asyncio
async def test_a_foreign_entry_is_undone_in_hex(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcdef") as s:
        hex_edit(s.document, 2, 4, b"XYZ")
        s.document.history.checkpoint()
        await s.pilot.pause()
        assert s.data() == b"abXYZef"
        await s.press("ctrl+z")
        assert s.data() == b"abcdef"
        assert s.view.cursor.offset == 2
        await s.press("ctrl+y")
        assert s.data() == b"abXYZef"
        assert s.view.cursor.offset == 5


@pytest.mark.asyncio
async def test_undo_is_not_guarded_by_read_only(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(4)) as s:
        await s.press("4")
        s.view.is_read_only = lambda: True
        await s.press("ctrl+z")
        assert s.data() == bytes(4)


@pytest.mark.asyncio
async def test_undo_during_an_edit_lock_is_refused_and_works_after_unlock(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(4)) as s:
        await s.press("4")
        s.document.lock_edits("saving")
        await s.press("ctrl+z")
        assert s.data() == bytes([0x40, 0, 0, 0])
        s.bell.assert_called_once()
        assert "Edit refused: saving" in s.notify.call_args.args[0]
        assert s.notify.call_args.kwargs["severity"] == "warning"
        assert s.document.history.peek_undo() is not None
        s.document.unlock_edits("saving")
        await s.press("ctrl+z")
        assert s.data() == bytes(4)


@pytest.mark.asyncio
async def test_a_rolled_back_step_reports_the_failed_state(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(4)) as s:
        reported: list[SourceChanged] = []
        s.view.on_source_changed = reported.append
        await s.press("4")
        with patch.object(s.document.history, "undo", side_effect=HistoryRolledBack("boom")):
            await s.press("ctrl+z")
        assert len(reported) == 1
        assert "could not be rolled back" in str(reported[0])
        s.bell.assert_called_once()
        s.notify.assert_called_once()


# -- REQ-13 cross-view acceptance -------------------------------------------------------------------------------------------

ORIGINAL = "one\ntwo\n"


def bytes_of(app: NovaEditApp) -> bytes:
    document = screen_of(app).document.editor.document
    return document.read_bytes(0, document.length)


@pytest.mark.asyncio
@pytest.mark.parametrize("undo_in", ["hex", "text"])
async def test_edit_in_text_edit_in_hex_two_undos_restore_the_original(tmp_path: Path, undo_in: str) -> None:
    path = tmp_path / "f.txt"
    path.write_text(ORIGINAL)
    app = app_for(tmp_path, path)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        original = bytes_of(app)
        await pilot.press("z")  # text edit at the start: "zone"
        await pilot.pause()
        edited_in_text = bytes_of(app)
        assert edited_in_text == b"z" + original
        await toggle(pilot, screen)
        await pilot.press("4", "1")  # hex edit: byte 1 (the cursor follows the text cursor) becomes 0x41
        await pilot.pause()
        edited_in_hex = bytes_of(app)
        assert edited_in_hex == b"zAne\ntwo\n"
        if undo_in == "text":
            await toggle(pilot, screen)
            assert screen.document.editor.text == "zAne\ntwo\n"
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert bytes_of(app) == edited_in_text
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert bytes_of(app) == original
        await toggle(pilot, screen)  # the other view shows the same bytes
        if undo_in == "text":
            assert screen.document.hex_mode
        else:
            assert not screen.document.hex_mode
            assert screen.document.editor.text == ORIGINAL
        await pilot.press("ctrl+y", "ctrl+y")
        await pilot.pause()
        assert bytes_of(app) == edited_in_hex


@pytest.mark.asyncio
async def test_hex_undo_through_the_standalone_screen_keys(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text(ORIGINAL)
    app = app_for(tmp_path, path)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        await pilot.press("4", "1")
        await pilot.pause()
        assert bytes_of(app) == b"Ane\ntwo\n"
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert bytes_of(app) == ORIGINAL.encode()
