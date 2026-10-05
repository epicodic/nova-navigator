"""The File and Search menu items run the new flows through the menu, as a user does (handover: menu items)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets.text_area import Selection

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen
from nova_widgets.file_dialog import FileDialogMode
from tests.nova_editor.dialog_helpers import HomeProvider, has_dialog, open_file_dialog
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.view_menu import FILE_MENU_INDEX, NARROW, SEARCH_MENU_INDEX, choose_item


@pytest.mark.asyncio
async def test_the_file_and_search_menu_items_run_the_new_flows(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x ab\nab\nab y\n")
    home = tmp_path / "home"
    home.mkdir()
    app = NovaEditApp(file_path=path, file_provider=HomeProvider(home))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        editor = app.editor
        await choose_item(pilot, screen, FILE_MENU_INDEX, "Open…")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        dialog = open_file_dialog(app)
        assert dialog is not None
        assert dialog.mode is FileDialogMode.OPEN
        await pilot.press("escape")
        await wait_until(pilot, lambda: not has_dialog(app))
        await choose_item(pilot, screen, FILE_MENU_INDEX, "Save As…")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        dialog = open_file_dialog(app)
        assert dialog is not None
        assert dialog.mode is FileDialogMode.SAVE
        await pilot.press("escape")
        await wait_until(pilot, lambda: not has_dialog(app))
        await choose_item(pilot, screen, SEARCH_MENU_INDEX, "Find…")
        await wait_until(pilot, lambda: app.find_popup.display)
        await pilot.press(*"ab", "enter")
        await wait_until(pilot, lambda: editor.selection == Selection((0, 2), (0, 4)))
        await choose_item(pilot, screen, SEARCH_MENU_INDEX, "Find Next")
        await wait_until(pilot, lambda: editor.selection == Selection((1, 0), (1, 2)))
        await choose_item(pilot, screen, SEARCH_MENU_INDEX, "Find Previous")
        await wait_until(pilot, lambda: editor.selection == Selection((0, 4), (0, 2)))
        await choose_item(pilot, screen, SEARCH_MENU_INDEX, "Go to…")
        await wait_until(pilot, lambda: app.goto_popup.display)
        assert app.focused is app.goto_popup.input
