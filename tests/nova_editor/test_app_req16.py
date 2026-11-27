"""REQ-16 acceptance bullets through the keys of nova_edit (open, save, goto, search, wrap)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp
from tests.nova_editor.dialog_helpers import box_message, box_title, wait_box
from tests.nova_editor.save_widget_helpers import wait_saved


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
        box = await wait_box(pilot, app)
        assert box_title(box) == "Save failed"
        assert box_message(box).startswith("Save failed (")
    assert not path.exists()
    assert not path.parent.exists()


@pytest.mark.asyncio
async def test_f10_toggles_wrap_and_keeps_the_cursor_on_the_same_character(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text(("word " * 60 + "\n") * 4)
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        assert app.editor is not None
        app.editor.move_cursor((2, 123))
        await pilot.pause()
        assert app.editor.soft_wrap is False
        await pilot.press("f10")
        await pilot.pause(0.1)
        assert app.editor.soft_wrap is True
        assert app.editor.cursor_location == (2, 123)
        await pilot.press("f10")
        await pilot.pause(0.1)
        assert app.editor.soft_wrap is False
        assert app.editor.cursor_location == (2, 123)
