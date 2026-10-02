"""REQ-16 acceptance bullets through the keys of nova_edit (open, save, goto, search, wrap)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets.text_area import Selection

from nova_editor.app import NovaEditApp, SaveBar
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import wait_saved

LINES = "".join(f"line {i}\n" for i in range(1, 51))


@pytest.mark.asyncio
async def test_a_missing_path_opens_an_empty_buffer_that_saves_to_that_path(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        assert app.editor.text == ""
        await pilot.press("h", "i", "ctrl+s")
        await wait_saved(pilot, app.editor)
    assert path.read_bytes() == b"hi"


@pytest.mark.asyncio
async def test_a_missing_parent_directory_is_a_visible_failure_and_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "no-such-dir" / "new.txt"
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+s")
        bar = app.query_one(SaveBar)
        await wait_until(pilot, lambda: bar.line.startswith("Save failed"))
        assert bar.failed
    assert not path.exists()
    assert not path.parent.exists()


@pytest.mark.asyncio
async def test_goto_line_and_byte_through_ctrl_g(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text(LINES)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        await pilot.press("ctrl+g", "3", "0", "enter")
        await wait_until(pilot, lambda: app.editor is not None and app.editor.cursor_location == (29, 0))
        await pilot.press("ctrl+g", "@", "1", "2", "enter")
        await wait_until(pilot, lambda: app.editor is not None and app.editor.cursor_byte_offset == 12)
        assert app.editor.cursor_location == (1, 5)


@pytest.mark.asyncio
async def test_an_out_of_range_goto_is_rejected_with_a_message_and_the_cursor_stays(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text(LINES)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        await pilot.press("ctrl+g", "5", "enter")
        await wait_until(pilot, lambda: app.editor is not None and app.editor.cursor_location == (4, 0))
        before = app.editor.cursor_location
        await pilot.press("ctrl+g", "9", "9", "9", "9", "9", "enter")
        await wait_until(pilot, lambda: any("line" in note.message.lower() for note in app._notifications))
        assert app.editor.cursor_location == before
        assert any(note.severity == "warning" for note in app._notifications)


@pytest.mark.asyncio
async def test_f7_searches_and_selects_the_match(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text(LINES)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        await pilot.press("f7")
        await pilot.press(*"line 42", "enter")
        await wait_until(pilot, lambda: app.editor is not None and not app.editor.searching and app.editor.selection == Selection((41, 0), (41, 7)))


@pytest.mark.asyncio
async def test_f4_toggles_wrap_and_keeps_the_cursor_on_the_same_character(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text(("word " * 60 + "\n") * 4)
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        assert app.editor is not None
        app.editor.move_cursor((2, 123))
        await pilot.pause()
        assert app.editor.soft_wrap is False
        await pilot.press("f4")
        await pilot.pause(0.1)
        assert app.editor.soft_wrap is True
        assert app.editor.cursor_location == (2, 123)
        await pilot.press("f4")
        await pilot.pause(0.1)
        assert app.editor.soft_wrap is False
        assert app.editor.cursor_location == (2, 123)
