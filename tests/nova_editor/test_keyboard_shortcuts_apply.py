"""Saving in the keyboard shortcuts dialog writes the standalone file and applies at once (REQ-3, REQ-5, REQ-6)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.pilot import Pilot

from nova_editor.app import build_app
from nova_editor.screen import EditorScreen
from nova_widgets.data_table import DataTable
from nova_widgets.keybindings_config import KeybindingsConfig
from nova_widgets.keymap import HintBar
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.test_keyboard_shortcuts import WIDE, dialog_of, open_dialog
from tests.nova_editor.view_menu import SEARCH_MENU_INDEX, open_view_menu


def row_of(screen: EditorScreen, action_id: str) -> int:
    ids = [a.id for a in screen.ACTIONS if a.id is not None and a.id.startswith("editor.")]
    return ids.index(action_id)


async def assign(
    pilot: Pilot[None],
    app: App[None],
    screen: EditorScreen,
    action_id: str,
    key: str,
) -> None:
    """Open the dialog, give `action_id` the key `key`, press OK and wait until the dialog is gone."""
    dialog = await open_dialog(pilot, app, screen)
    dialog.query(DataTable).first().move_cursor(row=row_of(screen, action_id))
    await pilot.press("space", key, "enter", "enter")
    await wait_until(pilot, lambda: dialog_of(app) is None)
    await pilot.pause()


async def unbind(
    pilot: Pilot[None],
    app: App[None],
    screen: EditorScreen,
    action_id: str,
) -> None:
    dialog = await open_dialog(pilot, app, screen)
    dialog.query(DataTable).first().move_cursor(row=row_of(screen, action_id))
    await pilot.press("delete", "enter")
    await wait_until(pilot, lambda: dialog_of(app) is None)
    await pilot.pause()


def effective(config_dir: Path, screen: EditorScreen) -> dict[str, str]:
    return {k: str(v) for k, v in KeybindingsConfig(config_dir).resolve(screen.ACTIONS).items()}


@pytest.mark.asyncio
async def test_save_writes_the_file_in_the_injected_dir_and_the_new_key_works_at_once(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "cfg"
    app = build_app(["--config-dir", str(config_dir)])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await assign(pilot, app, screen, "editor.find", "ctrl+k")
        assert (config_dir / "keybindings.toml").is_file()
        assert effective(config_dir, screen)["editor.find"] == "ctrl+k"
        await pilot.press("ctrl+f")
        await pilot.pause()
        assert not screen.find_popup.display
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert screen.find_popup.display


@pytest.mark.asyncio
async def test_save_updates_the_menu_label_and_the_hint_bar(tmp_path: Path) -> None:
    app = build_app(["--config-dir", str(tmp_path / "cfg")])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await assign(pilot, app, screen, "editor.find", "ctrl+k")
        menu = await open_view_menu(pilot, screen, SEARCH_MENU_INDEX)
        row = menu.render_line(1).text
        assert "Ctrl+K" in row
        assert "Ctrl+F" not in row
        await pilot.press("escape")
        hints = str(screen.query_one(HintBar).render())
        assert "Ctrl+K" in hints
        assert "Ctrl+F" not in hints


@pytest.mark.asyncio
async def test_unbinding_in_the_dialog_unmaps_the_key_the_label_and_the_hint(
    tmp_path: Path,
) -> None:
    app = build_app(["--config-dir", str(tmp_path / "cfg")])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await unbind(pilot, app, screen, "editor.goto")
        assert "editor.goto" not in effective(tmp_path / "cfg", screen)
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert not screen.goto_popup.display
        assert "Ctrl+G" not in str(screen.query_one(HintBar).render())
        assert {a.id: a.shortcut_label for a in screen.ACTIONS}["editor.goto"] == ""


@pytest.mark.asyncio
async def test_a_second_save_keeps_an_earlier_unmap(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    app = build_app(["--config-dir", str(config_dir)])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await unbind(pilot, app, screen, "editor.goto")
        await assign(pilot, app, screen, "editor.find", "ctrl+k")
        assert "editor.goto" not in effective(config_dir, screen)
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert not screen.goto_popup.display


@pytest.mark.asyncio
async def test_moving_an_editing_action_swallows_its_old_key_at_once(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("base")
    app = build_app(["--config-dir", str(tmp_path / "cfg"), str(source)])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await assign(pilot, app, screen, "editor.undo", "ctrl+shift+z")
        editor = screen.document.editor
        editor.focus()
        await pilot.press("x")
        await pilot.pause()
        assert editor.text.startswith("x")
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert editor.text.startswith("x")
        await pilot.press("ctrl+shift+z")
        await pilot.pause()
        assert not editor.text.startswith("x")


@pytest.mark.asyncio
async def test_unbinding_an_editing_action_swallows_its_key(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("base")
    app = build_app(["--config-dir", str(tmp_path / "cfg"), str(source)])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await unbind(pilot, app, screen, "editor.undo")
        editor = screen.document.editor
        editor.focus()
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert editor.text.startswith("x")


@pytest.mark.asyncio
async def test_the_navigator_config_is_never_read_or_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    nav_dir = home / ".config" / "nova-navigator"
    nav_dir.mkdir(parents=True)
    nav_file = nav_dir / "keybindings.toml"
    nav_file.write_text('[bindings]\n"editor.find" = "ctrl+j"\n')
    before = (
        nav_file.read_bytes(),
        nav_file.stat().st_mtime_ns,
        sorted(p.name for p in nav_dir.iterdir()),
    )
    monkeypatch.setenv("HOME", str(home))
    app = build_app([])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        assert {a.id: a.shortcut_label for a in screen.ACTIONS}["editor.find"] == "Ctrl+F"
        await assign(pilot, app, screen, "editor.find", "ctrl+k")
    assert (home / ".config" / "nova-edit" / "keybindings.toml").is_file()
    assert before == (
        nav_file.read_bytes(),
        nav_file.stat().st_mtime_ns,
        sorted(p.name for p in nav_dir.iterdir()),
    )
    assert sorted(p.name for p in (home / ".config").iterdir()) == ["nova-edit", "nova-navigator"]
