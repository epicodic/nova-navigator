"""The Find popup of the editor screen (REQ-17): its controls, the menu items, the buttons, switching with Go to and the places of the status texts."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Input
from textual.widgets.text_area import Selection

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen
from nova_editor.status_line import StatusLine
from nova_widgets.flat_widgets import Checkbox
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.view_menu import NARROW, SEARCH_MENU_INDEX, choose_item

THREE = "x ab\nab\nab y\n"
ALLOWED_IDS = {"find_label", "find_case", "find_previous", "find_next", "find_status"}


def make_file(tmp_path: Path, text: str = THREE) -> Path:
    path = tmp_path / "f.txt"
    path.write_text(text)
    return path


async def click_button(pilot: Pilot[None], button: Button) -> None:
    """Click `button` once its previous press is over (a `Button` ignores a click while it shows its active state)."""
    await wait_until(pilot, lambda: not button.has_class("-active"))
    await pilot.click(button)


@pytest.mark.asyncio
async def test_the_popup_offers_find_options_only(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        popup = app.find_popup
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: popup.display)
        assert len(popup.query(Input)) == 1  # the needle: no replacement field
        assert [str(box.label) for box in popup.query(Checkbox)] == ["Match case"]  # no regular expression, no whole word
        assert [str(button.label) for button in popup.query(Button)] == ["Previous", "Next"]
        assert {widget.id for widget in popup.query("*") if widget.id} <= ALLOWED_IDS
        assert popup.case_sensitive  # S0001 REQ-8: case-sensitive by default
        for control in popup.query("Input, Checkbox, Button"):
            assert popup.region.contains_region(control.region)


@pytest.mark.asyncio
async def test_the_search_menu_items_open_the_popup_and_search(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        editor = app.editor
        await choose_item(pilot, screen, SEARCH_MENU_INDEX, "Find…")
        await wait_until(pilot, lambda: app.find_popup.display)
        assert app.focused is app.find_popup.input
        await pilot.press(*"ab", "enter")
        await wait_until(pilot, lambda: editor.selection == Selection((0, 2), (0, 4)))
        await choose_item(pilot, screen, SEARCH_MENU_INDEX, "Find Next")
        await wait_until(pilot, lambda: editor.selection == Selection((1, 0), (1, 2)))
        await choose_item(pilot, screen, SEARCH_MENU_INDEX, "Find Previous")
        await wait_until(pilot, lambda: editor.selection == Selection((0, 4), (0, 2)))


@pytest.mark.asyncio
async def test_next_and_previous_buttons_and_the_f3_keys_move_between_matches(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        editor = app.editor
        popup = app.find_popup
        await pilot.press("ctrl+f", *"ab", "enter")
        await wait_until(pilot, lambda: editor.selection == Selection((0, 2), (0, 4)))
        await click_button(pilot, popup.query_one("#find_next", Button))
        await wait_until(pilot, lambda: editor.selection == Selection((1, 0), (1, 2)))
        await click_button(pilot, popup.query_one("#find_next", Button))
        await wait_until(pilot, lambda: editor.selection == Selection((2, 0), (2, 2)))
        await click_button(pilot, popup.query_one("#find_previous", Button))
        await wait_until(pilot, lambda: editor.selection == Selection((1, 2), (1, 0)))
        await pilot.press("ctrl+f")  # F3 from the focused popup input
        await pilot.press("f3")
        await wait_until(pilot, lambda: editor.selection == Selection((2, 0), (2, 2)))
        await pilot.press("f3")
        await wait_until(pilot, lambda: editor.selection == Selection((0, 2), (0, 4)))
        await wait_until(pilot, lambda: popup.status == "Wrapped to the top")
        await pilot.press("shift+f3")
        await wait_until(pilot, lambda: editor.selection == Selection((2, 2), (2, 0)))
        await wait_until(pilot, lambda: popup.status == "Wrapped to the bottom")
        assert popup.display


@pytest.mark.asyncio
async def test_clicking_the_checkbox_toggles_the_case_option(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        popup = app.find_popup
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: popup.display)
        await pilot.click(popup.query_one("#find_case", Checkbox))
        await wait_until(pilot, lambda: not popup.case_sensitive)
        await pilot.press("alt+c")
        await wait_until(pilot, lambda: popup.case_sensitive)


@pytest.mark.asyncio
async def test_the_popup_is_prefilled_with_the_last_needle(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        popup = app.find_popup
        await pilot.press("ctrl+f", *"ab", "enter")
        await wait_until(pilot, lambda: popup.status == "Found")
        await pilot.press("escape")
        await wait_until(pilot, lambda: not popup.display)
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: popup.display)
        assert popup.input.value == "ab"
        assert popup.input.selected_text == "ab"  # selected, so typing replaces it
        assert popup.status == ""  # a new opening starts with an empty status row


@pytest.mark.asyncio
async def test_ctrl_g_and_ctrl_f_switch_the_popups(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: app.find_popup.display)
        await pilot.press("ctrl+g")
        await wait_until(pilot, lambda: app.goto_popup.display)
        assert not app.find_popup.display
        assert app.focused is app.goto_popup.input
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: app.find_popup.display)
        assert not app.goto_popup.display
        assert app.focused is app.find_popup.input


@pytest.mark.asyncio
async def test_the_search_texts_go_to_the_popup_when_it_is_open_and_to_the_note_when_it_is_closed(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        popup = app.find_popup
        status = app.query_one(StatusLine)
        await pilot.press("ctrl+f", *"ab", "enter")
        await wait_until(pilot, lambda: popup.status == "Found")
        assert status.note is None  # the popup shows it
        await pilot.press("escape")
        await wait_until(pilot, lambda: not popup.display)
        await pilot.press("f3")
        await wait_until(pilot, lambda: status.note == "Found")  # the popup is closed: the status line shows it
