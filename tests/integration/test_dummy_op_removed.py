"""Tests that the Dummy Op development action is gone."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable

from nova_navigator.filemanager import tasks
from nova_navigator.nova_navigator import MainScreen
from nova_widgets import KeybindingsConfig, KeybindingsDialog
from nova_widgets.keymap.key_sequence import KeySequence
from tests.integration.conftest import AppCtx


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ctrl_d_starts_no_job(app_ctx: AppCtx) -> None:
    """Ctrl+D no longer starts a job."""
    with patch.object(app_ctx.app, "start_job", new=AsyncMock()) as start:
        await app_ctx.pilot.press("ctrl+d")
        await app_ctx.pilot.pause()
    start.assert_not_called()


def test_main_screen_has_no_dummy_action() -> None:
    """MainScreen.ACTIONS has no Dummy Op action and nothing bound to ctrl+d."""
    ctrl_d = KeySequence.parse("ctrl+d")
    for action in MainScreen.ACTIONS:
        assert action.id != "app.start_dummy_operation"
        assert action.text != "Dummy Op"
        assert action.initial_shortcut != ctrl_d


@pytest.mark.asyncio
async def test_keybindings_dialog_has_no_dummy_row(tmp_path: Path) -> None:
    """The keybindings dialog lists no Dummy Op row."""
    dialog = KeybindingsDialog(actions=[*MainScreen.ACTIONS], config=KeybindingsConfig(tmp_path))

    class DialogApp(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    app = DialogApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query(DataTable).first()
        assert table.row_count > 0
        for index in range(table.row_count):
            assert all("Dummy Op" not in str(cell) for cell in table.get_row_at(index))


def test_dummy_task_is_gone() -> None:
    """The simulated failing task and its constants are removed."""
    assert not hasattr(tasks, "dummy_task")
    assert not [name for name in dir(tasks) if name.startswith("_DUMMY")]


def test_main_screen_has_no_start_dummy_operation_handler() -> None:
    """The action handler is removed."""
    assert not hasattr(MainScreen, "action_start_dummy_operation")


def test_stale_dummy_override_is_ignored(tmp_path: Path) -> None:
    """A leftover override for the removed action does not resolve."""
    (tmp_path / "keybindings.toml").write_text('[bindings]\n"app.start_dummy_operation" = "ctrl+d"\n')
    resolved = KeybindingsConfig(tmp_path).resolve(MainScreen.ACTIONS)
    assert "app.start_dummy_operation" not in resolved
