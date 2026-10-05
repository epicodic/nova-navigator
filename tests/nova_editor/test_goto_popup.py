"""The Go to popup of the editor screen (REQ-16, S0001 REQ-7): opening, the forms, Enter, Escape and the reports in the popup."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets.text_area import Selection

from nova_editor.app import NovaEditApp
from nova_editor.goto_popup import HINT, INVALID
from nova_editor.screen import EditorScreen
from nova_editor.status_line import StatusLine
from tests.nova_editor.helpers_view import GatedRowEditor, GatedRowsEditor, wait_until
from tests.nova_editor.view_menu import NARROW, SEARCH_MENU_INDEX, choose_item

LINES = "".join(f"line {i}\n" for i in range(1, 51))


def make_file(tmp_path: Path, text: str = LINES) -> Path:
    path = tmp_path / "f.txt"
    path.write_text(text)
    return path


@pytest.mark.asyncio
async def test_ctrl_g_opens_the_popup_at_the_top_right_of_the_editor_and_focuses_it(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        popup = app.goto_popup
        assert not popup.display
        await pilot.press("ctrl+g")
        await wait_until(pilot, lambda: popup.display)
        assert app.focused is popup.input
        assert popup.shown == HINT
        editor = app.editor
        assert popup.region.right == editor.region.right
        assert popup.region.y == editor.region.y
        assert popup.region.bottom <= app.query_one(StatusLine).region.y  # the status line stays visible while the popup is open


@pytest.mark.asyncio
async def test_the_search_menu_item_opens_the_popup(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await choose_item(pilot, screen, SEARCH_MENU_INDEX, "Go to…")
        await wait_until(pilot, lambda: app.goto_popup.display)
        assert app.focused is app.goto_popup.input


@pytest.mark.asyncio
async def test_enter_jumps_and_closes_the_popup(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        popup = app.goto_popup
        await pilot.press("ctrl+g", "3", "0", "enter")  # a line
        await wait_until(pilot, lambda: app.editor.cursor_location == (29, 0))
        await wait_until(pilot, lambda: not popup.display)
        assert app.focused is app.editor
        await pilot.press("ctrl+g", "@", "1", "2", "enter")  # a byte offset
        await wait_until(pilot, lambda: app.editor.cursor_byte_offset == 12)
        assert app.editor.cursor_location == (1, 5)
        await wait_until(pilot, lambda: not popup.display)
        assert app.focused is app.editor


@pytest.mark.asyncio
async def test_escape_closes_the_popup_and_focuses_the_editor(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        popup = app.goto_popup
        await pilot.press("ctrl+g", "4", "escape")
        await wait_until(pilot, lambda: not popup.display)
        assert app.focused is app.editor
        assert app.editor.cursor_location[0] == 0  # nothing was jumped to
        await pilot.press("ctrl+g")
        await wait_until(pilot, lambda: popup.display)
        assert popup.input.value == "4"  # Escape does not clear the input


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["abc", "@-1", "-2", "@x", ""])
async def test_invalid_input_is_reported_in_the_popup(tmp_path: Path, text: str) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        popup = app.goto_popup
        await pilot.press("ctrl+g", *text, "enter")
        await wait_until(pilot, lambda: popup.shown == INVALID)
        assert popup.display
        assert app.focused is popup.input
        assert app.editor.cursor_location == (0, 0)
        await pilot.press("escape")
        await pilot.press("ctrl+g")
        await wait_until(pilot, lambda: popup.shown == HINT)  # a new opening starts with the hint


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("99999", "line 99999 is beyond the last line"),
        ("0", "line 0 is not a line number (lines start at 1)"),
        ("@99999", "byte offset 99999 is beyond the end of the file"),
    ],
)
async def test_an_out_of_range_target_keeps_the_popup_open_with_the_reason(tmp_path: Path, text: str, reason: str) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        popup = app.goto_popup
        await pilot.press("ctrl+g", *text, "enter")
        await wait_until(pilot, lambda: popup.shown.startswith(reason))
        assert popup.display
        assert app.focused is popup.input
        assert app.editor.cursor_location == (0, 0)
        await pilot.press("5", "enter")  # the rejected text was selected: typing replaces it
        await wait_until(pilot, lambda: app.editor.cursor_location == (4, 0))
        await wait_until(pilot, lambda: not popup.display)


@pytest.mark.asyncio
async def test_ctrl_g_with_the_popup_open_keeps_it_open(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        popup = app.goto_popup
        await pilot.press("ctrl+g", "ctrl+g")
        await pilot.pause()
        assert popup.display
        assert app.focused is popup.input


@pytest.mark.asyncio
async def test_a_click_into_the_editor_closes_the_popup(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        popup = app.goto_popup
        await pilot.press("ctrl+g")
        await wait_until(pilot, lambda: popup.display)
        await pilot.click(app.editor, offset=(2, 5))
        await wait_until(pilot, lambda: not popup.display)
        assert app.focused is app.editor


@pytest.mark.asyncio
async def test_f3_still_works_while_the_popup_is_open(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        app.search("line 4")
        await wait_until(pilot, lambda: not app.editor.searching and app.editor.selection == Selection((3, 0), (3, 6)))
        await pilot.press("ctrl+g", "f3")
        await wait_until(pilot, lambda: app.editor.selection == Selection((39, 0), (39, 6)))
        assert app.goto_popup.display
        assert app.focused is app.goto_popup.input


@pytest.mark.asyncio
async def test_a_target_beyond_the_scan_closes_the_popup_and_the_status_line_shows_the_progress(tmp_path: Path) -> None:
    path = make_file(tmp_path, "".join(f"row {i}\n" for i in range(3000)))
    GatedRowEditor.gates.clear()
    app = NovaEditApp(file_path=path, editor_class=GatedRowsEditor)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        gate = GatedRowEditor.gates[0]
        await wait_until(pilot, gate.blocked.is_set)  # the scan is held at 2 KiB
        await pilot.press("ctrl+g", *"2500", "enter")
        status = app.query_one(StatusLine)
        await wait_until(pilot, lambda: "Goto" in status.text)
        assert not app.goto_popup.display
        assert app.focused is app.editor
        gate.release()
        await wait_until(pilot, lambda: app.editor.cursor_location == (2499, 0))
        await wait_until(pilot, lambda: "Goto" not in status.text)
