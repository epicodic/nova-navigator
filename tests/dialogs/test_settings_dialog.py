"""Tests for SettingsDialog."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from nova_navigator.config.settings import Settings
from nova_navigator.dialogs.settings_dialog import SettingsDialog
from nova_navigator.widgets.model_editor import ModelEditor


def _make_app(dialog: SettingsDialog) -> type[App[None]]:
    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    return _App


@pytest.mark.asyncio
async def test_settings_dialog_shows_no_proxy_field() -> None:
    app = _make_app(SettingsDialog(Settings()))()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        names = [str(label.render()) for label in app.screen.query(".me-name").results(Label)]
        network = app.screen.query_one("#editor_network", ModelEditor)
        assert "Ssh Timeout" in names
        assert "ssh_timeout" in network._field_controls
        assert not any("proxy" in name.lower() for name in names)
        assert "proxy" not in network._field_controls


@pytest.mark.asyncio
async def test_settings_dialog_still_shows_confirm_delete_row() -> None:
    app = _make_app(SettingsDialog(Settings()))()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        general = app.screen.query_one("#editor_general", ModelEditor)
        assert "confirm_delete" in general._field_controls


@pytest.mark.asyncio
async def test_settings_dialog_shows_include_folders_row() -> None:
    app = _make_app(SettingsDialog(Settings()))()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        selection = app.screen.query_one("#editor_selection", ModelEditor)
        assert "include_folders" in selection._field_controls
