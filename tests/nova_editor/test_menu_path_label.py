"""The path label on the right of the menu bar must not cover menu entries at narrow terminals."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen
from tests.nova_editor.view_menu import open_view_menu

NARROW = (80, 24)
WIDE = (200, 24)


def make_file_with_long_path(tmp_path: Path) -> Path:
    """Create a file in a deep tmp_path subdirectory so the full path is >100 chars."""
    deep_dir = tmp_path / "a_very_long_directory_name" / "another_long_name" / "yet_another_long_name"
    deep_dir.mkdir(parents=True, exist_ok=True)
    path = deep_dir / "test_file.txt"
    path.write_text("content")
    return path


@pytest.mark.asyncio
async def test_all_four_menus_accessible_at_narrow_width(tmp_path: Path) -> None:
    """At 80 columns with a long path, all four menus (File, Edit, Search, View) must be clickable."""
    path = make_file_with_long_path(tmp_path)
    app = NovaEditApp(file_path=path)

    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)

        # Test each of the four menus: File (0), Edit (1), Search (2), View (3)
        for menu_index in range(4):
            menu = await open_view_menu(pilot, screen, menu_index=menu_index)
            assert menu in list(screen.query("Menu"))
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_path_label_does_not_overlap_menu_entries(tmp_path: Path) -> None:
    """The path label region must not overlap with menu entry regions."""
    path = make_file_with_long_path(tmp_path)
    app = NovaEditApp(file_path=path)

    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)

        # Get the menu bar items and the path label
        menu_items = list(screen.query("MenuBarItem"))
        path_label = screen.query_one("#path_label")

        # Find the rightmost edge of all menu items
        menu_right_edge = max(item.region.right for item in menu_items)

        # The path label region should start at or after the right edge of the last menu item
        # (allowing for some spacing/padding)
        if path_label.region.width > 0:
            # If the label is visible, it must not overlap
            assert path_label.region.x >= menu_right_edge, f"Path label overlaps with menu items: label x={path_label.region.x}, menu right edge={menu_right_edge}"


@pytest.mark.asyncio
async def test_path_label_truncation_at_narrow_width(tmp_path: Path) -> None:
    """The path label should show the end of the path with ellipsis or be empty at narrow width."""
    path = make_file_with_long_path(tmp_path)
    full_path_str = str(path)
    assert len(full_path_str) > 100, "Test path should be > 100 chars"

    app = NovaEditApp(file_path=path)

    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)

        path_label = screen.query_one("#path_label")
        rendered_text = path_label.render().plain

        # The label should either be empty or show the end of the path with ellipsis
        if rendered_text:
            # If not empty, it should contain the ellipsis and be shorter than the full path
            assert "…" in rendered_text, "Truncated path should contain ellipsis"
            assert len(rendered_text) < len(full_path_str), "Rendered text should be shorter than full path"
            # The rendered text should be a suffix of the full path (possibly with ellipsis prepended)
            assert rendered_text.lstrip("…") in full_path_str or rendered_text.lstrip("…") == "", f"Truncated text should be from the end of path: {rendered_text}"


@pytest.mark.asyncio
async def test_path_label_full_at_wide_width(tmp_path: Path) -> None:
    """At wide terminal width, the full path should be displayed."""
    path = make_file_with_long_path(tmp_path)
    full_path_str = str(path)

    app = NovaEditApp(file_path=path)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)

        path_label = screen.query_one("#path_label")
        rendered_text = path_label.render().plain

        # At wide width, should show full path
        assert rendered_text == full_path_str, f"At wide width, should show full path. Got: {rendered_text}, Expected: {full_path_str}"


@pytest.mark.asyncio
async def test_path_label_updates_on_resize(tmp_path: Path) -> None:
    """The path label should update when terminal is resized."""
    path = make_file_with_long_path(tmp_path)
    full_path_str = str(path)

    app = NovaEditApp(file_path=path)

    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)

        path_label = screen.query_one("#path_label")

        # Resize to wide
        await pilot.resize_terminal(WIDE[0], WIDE[1])
        await pilot.pause()

        wide_text = path_label.render().plain
        assert wide_text == full_path_str, "At wide width should show full path"

        # Resize back to narrow
        await pilot.resize_terminal(NARROW[0], NARROW[1])
        await pilot.pause()

        narrow_text_again = path_label.render().plain
        # After resizing back, should be similar to the initial narrow state
        # (may or may not have ellipsis depending on exact layout)
        if narrow_text_again:
            assert len(narrow_text_again) <= len(full_path_str)
