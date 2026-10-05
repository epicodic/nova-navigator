"""nova_edit reads its own key file: missing file, override, unmap, the config dir option (REQ-3, REQ-5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp, build_app, default_config_dir
from nova_editor.screen import EditorScreen
from nova_widgets.menu import Menu
from tests.nova_editor.view_menu import SEARCH_MENU_INDEX, open_view_menu

WIDE = (160, 24)


def write_file(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "keybindings.toml").write_text(text)
    return directory


def menu_row(menu: Menu, text: str) -> str:
    for index, action in enumerate(menu.actions):
        if action.text == text:
            return menu.render_line(index + 1).text
    raise AssertionError(text)


def test_the_default_config_dir_is_the_nova_edit_dir_of_the_home(tmp_path: Path) -> None:
    assert default_config_dir(tmp_path) == tmp_path / ".config" / "nova-edit"


@pytest.mark.asyncio
async def test_a_missing_file_means_the_defaults(tmp_path: Path) -> None:
    app = build_app(["--config-dir", str(tmp_path / "none")])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        assert {a.id: a.shortcut_label for a in screen.ACTIONS}["editor.find"] == "Ctrl+F"
        await pilot.press("ctrl+f")
        await pilot.pause()
        assert screen.find_popup.display


@pytest.mark.asyncio
async def test_an_override_file_changes_the_key_and_the_menu_label(tmp_path: Path) -> None:
    directory = write_file(tmp_path / "cfg", '[bindings]\n"editor.find" = "ctrl+k"\n')
    app = build_app(["--config-dir", str(directory)])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await pilot.press("ctrl+f")
        await pilot.pause()
        assert not screen.find_popup.display
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert screen.find_popup.display
        await pilot.press("escape")
        menu = await open_view_menu(pilot, screen, SEARCH_MENU_INDEX)
        assert "Ctrl+K" in menu_row(menu, "Find…")


@pytest.mark.asyncio
async def test_an_empty_override_unmaps_the_key(tmp_path: Path) -> None:
    directory = write_file(tmp_path / "cfg", '[bindings]\n"editor.goto" = ""\n')
    app = build_app(["--config-dir", str(directory)])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert not screen.goto_popup.display
        assert {a.id: a.shortcut_label for a in screen.ACTIONS}["editor.goto"] == ""


@pytest.mark.asyncio
async def test_without_the_option_the_file_in_the_home_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    write_file(tmp_path / ".config" / "nova-edit", '[bindings]\n"editor.find" = "ctrl+k"\n')
    app = build_app([])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert screen.find_popup.display


def test_the_app_class_still_runs_without_a_config(tmp_path: Path) -> None:
    assert NovaEditApp(path=tmp_path / "x.txt").keybindings is None
