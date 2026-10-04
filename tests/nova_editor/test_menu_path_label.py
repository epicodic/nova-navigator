"""The path label on the right of the menu bar must not cover menu entries at narrow terminals."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Static

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.view_menu import NARROW, open_view_menu

WIDE = (200, 24)
ELLIPSIS = "…"
MIN_PATH_LENGTH = 100


def make_file_with_long_path(tmp_path: Path) -> Path:
    """Create a file in a deep tmp_path subdirectory so the full path is >100 chars."""
    deep_dir = tmp_path / "a_very_long_directory_name" / "another_long_name" / "yet_another_long_name"
    deep_dir.mkdir(parents=True, exist_ok=True)
    path = deep_dir / "test_file.txt"
    path.write_text("content")
    assert len(str(path)) > MIN_PATH_LENGTH
    return path


def label_text(label: Static) -> str:
    """Return the text the label shows."""
    return str(label.content)


def last_item_right(screen: EditorScreen) -> int:
    """Return the right edge of the last menu bar entry."""
    return max(item.region.right for item in screen.query("MenuBarItem"))


@pytest.mark.asyncio
async def test_all_four_menus_accessible_at_narrow_width(tmp_path: Path) -> None:
    """At 80 columns with a long path, all four menus (File, Edit, Search, View) open by a real click."""
    app = NovaEditApp(file_path=make_file_with_long_path(tmp_path))

    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)

        for menu_index in range(4):
            menu = await open_view_menu(pilot, screen, menu_index=menu_index)
            assert menu in list(screen.query("Menu"))
            await pilot.press("escape")
            await wait_until(pilot, lambda: not list(screen.query("Menu")))


@pytest.mark.asyncio
async def test_path_label_is_visible_truncated_and_clear_of_the_menu_entries(tmp_path: Path) -> None:
    """At 80 columns the label shows the end of the path behind an ellipsis, right of the last entry."""
    path = make_file_with_long_path(tmp_path)
    full_path = str(path)
    app = NovaEditApp(file_path=path)

    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        label = screen.query_one("#path_label", Static)
        await wait_until(pilot, lambda: label_text(label) != "")

        text = label_text(label)
        assert label.region.width > 0
        assert text.startswith(ELLIPSIS)
        assert len(text) < len(full_path)
        assert full_path.endswith(text[1:])
        assert label.region.x >= last_item_right(screen)
        assert label.region.right <= screen.size.width
        assert app.sub_title == full_path


@pytest.mark.asyncio
async def test_path_label_follows_the_terminal_width(tmp_path: Path) -> None:
    """The full path shows at 200 columns; back at 80 columns the same truncated text returns."""
    path = make_file_with_long_path(tmp_path)
    full_path = str(path)
    app = NovaEditApp(file_path=path)

    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        label = screen.query_one("#path_label", Static)
        await wait_until(pilot, lambda: label_text(label) != "")
        narrow_text = label_text(label)
        assert narrow_text.startswith(ELLIPSIS)

        await pilot.resize_terminal(*WIDE)
        await wait_until(pilot, lambda: label_text(label) == full_path)
        assert label.region.x >= last_item_right(screen)
        assert label.region.right <= screen.size.width

        await pilot.resize_terminal(*NARROW)
        await wait_until(pilot, lambda: label_text(label) == narrow_text)
        assert label.region.width > 0
        assert label.region.x >= last_item_right(screen)
        assert label.region.right <= screen.size.width
