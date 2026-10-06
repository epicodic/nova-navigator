"""The Keyboard Shortcuts… entry: where it is, what the dialog lists (REQ-6)."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.text import Text
from textual.app import App
from textual.pilot import Pilot

from nova_editor.app import build_app
from nova_editor.screen import EditorScreen
from nova_widgets.data_table import DataTable
from nova_widgets.key_types import KeySequence
from nova_widgets.keybindings_dialog import KeybindingsDialog
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.screen_host import EditorScreenHost, write_keys
from tests.nova_editor.view_menu import open_view_menu

WIDE = (160, 24)
ITEM_ROW = 3
"""Row of Keyboard Shortcuts… in the opened View menu (Line Numbers 1, Wrap Mode 2)."""


def dialog_of(app: App[None]) -> KeybindingsDialog | None:
    return app.screen if isinstance(app.screen, KeybindingsDialog) else None


async def open_dialog(pilot: Pilot[None], app: App[None], screen: EditorScreen) -> KeybindingsDialog:
    menu = await open_view_menu(pilot, screen)
    await pilot.hover(menu, offset=(2, ITEM_ROW))
    await pilot.click(menu, offset=(2, ITEM_ROW))
    await wait_until(pilot, lambda: dialog_of(app) is not None)
    dialog = dialog_of(app)
    assert dialog is not None
    await pilot.pause()
    return dialog


def rows(dialog: KeybindingsDialog) -> dict[str, str]:
    table = dialog.query(DataTable).first()
    result: dict[str, str] = {}
    for index in range(table.row_count):
        label, keys = table.get_row_at(index)
        result[str(label)] = keys.plain.strip() if isinstance(keys, Text) else str(keys)
    return result


@pytest.mark.asyncio
async def test_the_view_menu_has_the_item_only_with_a_config(tmp_path: Path) -> None:
    host = EditorScreenHost(standalone=True)
    async with host.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert host.screen_instance is not None
        assert len(host.screen_instance.ACTIONS) == 18
        assert [a.id for a in host.screen_instance.menu_bar.actions[3].actions] == ["editor.line_numbers", "editor.wrap_mode"]
    app = build_app(["--config-dir", str(tmp_path)])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        assert len(screen.ACTIONS) == 19
        ids = [a.id for a in screen.menu_bar.actions[3].actions]
        assert ids == ["editor.line_numbers", "editor.wrap_mode", "editor.keyboard_shortcuts"]


@pytest.mark.asyncio
async def test_the_item_has_no_default_key(tmp_path: Path) -> None:
    app = build_app(["--config-dir", str(tmp_path)])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        action = next(a for a in screen.ACTIONS if a.id == "editor.keyboard_shortcuts")
        assert action.shortcut is None


@pytest.mark.asyncio
async def test_the_dialog_lists_every_editor_action_with_its_effective_binding(tmp_path: Path) -> None:
    (tmp_path / "keybindings.toml").write_text('[bindings]\n"editor.find" = "ctrl+k"\n"editor.goto" = ""\n')
    app = build_app(["--config-dir", str(tmp_path)])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        expected = [a.text for a in screen.ACTIONS if a.id is not None and a.id.startswith("editor.")]
        dialog = await open_dialog(pilot, app, screen)
        shown = rows(dialog)
        assert list(shown) == expected
        assert len(shown) == 19
        assert shown["Find…"] == "Ctrl+K"
        assert shown["Go to…"] == "(none)"
        assert shown["Save"] == "Ctrl+S"
        assert shown["Keyboard Shortcuts…"] == "(none)"


@pytest.mark.asyncio
async def test_escape_closes_the_dialog_and_changes_nothing(tmp_path: Path) -> None:
    app = build_app(["--config-dir", str(tmp_path)])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await open_dialog(pilot, app, screen)
        await pilot.press("escape")
        await wait_until(pilot, lambda: dialog_of(app) is None)
        assert not (tmp_path / "keybindings.toml").exists()
        assert {a.id: a.shortcut_label for a in screen.ACTIONS}["editor.find"] == "Ctrl+F"


@pytest.mark.asyncio
async def test_a_config_without_the_item_applies_overrides_and_offers_no_dialog(tmp_path: Path) -> None:
    config = write_keys(tmp_path, {"editor.find": KeySequence.parse("ctrl+k"), "editor.goto": None})
    host = EditorScreenHost(keybindings=config, keyboard_shortcuts_item=False, standalone=True)
    async with host.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        assert len(screen.ACTIONS) == 18
        assert [a.id for a in screen.menu_bar.actions[3].actions] == ["editor.line_numbers", "editor.wrap_mode"]
        labels = {a.id: a.shortcut_label for a in screen.ACTIONS}
        assert labels["editor.find"] == "Ctrl+K"
        assert labels["editor.goto"] == ""
        screen.action_keyboard_shortcuts()
        await pilot.pause(delay=0.3)
        assert host.screen is screen
