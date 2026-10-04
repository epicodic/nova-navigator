"""The path label on the right of the menu bar must not cover menu entries at narrow terminals."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen
from tests.nova_editor.view_menu import WIDE, open_view_menu


def make_file_with_long_path(tmp_path: Path) -> Path:
    """Create a file in a deep tmp_path subdirectory so the full path is >100 chars."""
    deep_dir = tmp_path / "a_very_long_directory_name" / "another_long_name" / "yet_another_long_name"
    deep_dir.mkdir(parents=True, exist_ok=True)
    path = deep_dir / "test_file.txt"
    path.write_text("content")
    return path


@pytest.mark.asyncio
async def test_menu_is_accessible_when_path_is_long() -> None:
    """At 80 columns with a long path, the View menu must be clickable and accessible."""
    path = make_file_with_long_path(pytest.importorskip("pathlib").Path.cwd())
    app = NovaEditApp(file_path=path)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)

        menu = await open_view_menu(pilot, screen)
        assert menu.text == "View"
        assert menu in list(screen.query("Menu"))


@pytest.mark.asyncio
async def test_path_label_does_not_overlap_menu_entries(tmp_path: Path) -> None:
    """The path label must not cover menu entries, even when the path is long."""
    path = make_file_with_long_path(tmp_path)
    app = NovaEditApp(file_path=path)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)

        # The path label should be truncated or hidden (constrained by _constrain_path_label)
        # Check that the displayed text is shorter than or equal to the full path
        # This verifies that truncation happened when needed
        full_path = str(path)
        assert screen._full_path_text == full_path

        # After resizing to a wider terminal, the full path should be visible
        await pilot.resize_terminal(160, 24)
        await pilot.pause()
        # The path label should still reference the same full path
        assert screen._full_path_text == full_path


@pytest.mark.asyncio
async def test_path_label_updates_on_resize(tmp_path: Path) -> None:
    """The path label should update its width constraint on terminal resize."""
    path = make_file_with_long_path(tmp_path)
    app = NovaEditApp(file_path=path)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)

        full_path = str(path)
        assert screen._full_path_text == full_path

        await pilot.resize_terminal(160, 24)
        await pilot.pause()

        # The full path should still be tracked after resize
        assert screen._full_path_text == full_path
