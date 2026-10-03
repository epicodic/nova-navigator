"""Tests for KeybindingsDialog."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_widgets import KeybindingsConfig, KeybindingsDialog
from nova_widgets.action import Action
from nova_widgets.keymap.key_sequence import KeyFormatStyle


def _make_actions() -> list[Action]:
    return [
        Action(
            "Copy",
            id="browser.copy",
            action="copy_or_move_files(False)",
            description="Copy files to the other panel",
            shortcut="f5",
            show=True,
        ),
        Action(
            "Move",
            id="browser.move",
            action="copy_or_move_files(True)",
            description="Move files to the other panel",
            shortcut="f6",
            show=True,
        ),
        Action(
            "Delete",
            id="browser.delete",
            action="delete_files",
            description="Delete selected files",
            shortcut="f8",
            show=True,
        ),
    ]


@pytest.mark.asyncio
async def test_keybindings_dialog_opens(tmp_path: Path) -> None:
    from textual.app import App, ComposeResult

    actions = _make_actions()
    cfg = KeybindingsConfig(tmp_path)
    dialog = KeybindingsDialog(actions=actions, config=cfg)

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    app = TestApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        # The dialog is the active screen when pushed
        assert isinstance(app.screen, KeybindingsDialog)


@pytest.mark.asyncio
async def test_keybindings_dialog_shows_actions(tmp_path: Path) -> None:
    from textual.app import App, ComposeResult
    from textual.widgets import DataTable

    actions = _make_actions()
    cfg = KeybindingsConfig(tmp_path)
    dialog = KeybindingsDialog(actions=actions, config=cfg)

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    app = TestApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        tables = list(app.screen.query(DataTable))
        assert len(tables) == 1
        # The table should have rows for each action
        table = tables[0]
        assert table.row_count == len(actions)


def test_keybindings_dialog_default_key_display_style(tmp_path: Path) -> None:
    actions = _make_actions()
    cfg = KeybindingsConfig(tmp_path)
    dialog = KeybindingsDialog(actions=actions, config=cfg, key_display_style=None)
    assert dialog._key_display_style == KeyFormatStyle.CLASSIC


def test_keybindings_dialog_forwards_key_display_style(tmp_path: Path) -> None:
    actions = _make_actions()
    cfg = KeybindingsConfig(tmp_path)
    dialog = KeybindingsDialog(
        actions=actions,
        config=cfg,
        key_display_style=KeyFormatStyle.EMACS,
    )
    assert dialog._key_display_style == KeyFormatStyle.EMACS
