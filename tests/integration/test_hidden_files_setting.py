"""Integration tests for applying and saving the ``show_hidden_files`` setting.

Each test starts a real app, toggles hidden files through the UI and reads ``settings.toml`` from disk.
"""

from __future__ import annotations

import contextlib
import copy
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.pilot import Pilot

from nova_navigator import nova_navigator as nova_navigator_module
from nova_navigator.config import conf_, loader
from nova_navigator.config.settings import Settings
from nova_navigator.nova_navigator import MainScreen, NovaNavigator
from nova_navigator.widgets.directory_browser import DirectoryBrowser
from nova_widgets import Response
from nova_widgets.menu import Menu

_ACTION_ID = "view.show_hidden_files"


@contextlib.asynccontextmanager
async def _session(config_dir: Path) -> AsyncIterator[tuple[NovaNavigator, Pilot[None], MainScreen]]:
    app = NovaNavigator(config_dir=config_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        yield app, pilot, app._main_screen


def _assert_hidden(screen: MainScreen, expected: bool) -> None:
    assert screen._left_panel.show_hidden_files is expected
    assert screen._right_panel.show_hidden_files is expected
    assert screen._act(_ACTION_ID).checked is expected


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    return config


@pytest.fixture
def settings_file() -> Path:
    """The settings.toml path the app reads (an autouse fixture redirects the config dir per test)."""
    path = loader._APP_CONFIG_DIR / "settings.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.asyncio
@pytest.mark.integration
async def test_setting_applied_at_start(config_dir: Path, settings_file: Path) -> None:
    settings_file.write_text("[general]\nshow_hidden_files = true\n")
    async with _session(config_dir) as (_app, _pilot, screen):
        _assert_hidden(screen, True)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_setting_applied_at_start_when_edited_by_hand(config_dir: Path, settings_file: Path) -> None:
    settings_file.write_text("# my notes\n[general]\n# keep\nshow_hidden_files = true\nconfirm_delete = false\n")
    async with _session(config_dir) as (_app, _pilot, screen):
        _assert_hidden(screen, True)
    assert "show_hidden_files = true" in settings_file.read_text()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("settings_file")
async def test_ctrl_h_saves_setting(config_dir: Path) -> None:
    async with _session(config_dir) as (_app, pilot, screen):
        _assert_hidden(screen, False)

        await pilot.press("ctrl+h")
        await pilot.pause()
        _assert_hidden(screen, True)
        assert Settings.load().general.show_hidden_files is True

        await pilot.press("ctrl+h")
        await pilot.pause()
        _assert_hidden(screen, False)
        assert Settings.load().general.show_hidden_files is False


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("settings_file")
async def test_menu_toggle_saves_setting(config_dir: Path) -> None:
    async with _session(config_dir) as (_app, pilot, screen):
        action = screen._act(_ACTION_ID)
        action.set_checked(True)  # the menu flips the checked state before running the handler
        with patch.object(Menu, "exec", new=AsyncMock(return_value=action)):
            screen._left_panel.post_message(DirectoryBrowser.ContextMenu(screen._left_panel, None))
            await pilot.pause(delay=0.3)
        _assert_hidden(screen, True)
        assert Settings.load().general.show_hidden_files is True


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("settings_file")
async def test_settings_dialog_applies_at_once_and_after_restart(config_dir: Path) -> None:
    async with _session(config_dir) as (_app, pilot, screen):
        _assert_hidden(screen, False)
        new_settings = copy.deepcopy(conf_.settings)
        new_settings.general.show_hidden_files = True
        dialog = MagicMock()
        dialog.run = AsyncMock(return_value=Response.OK)
        dialog.config = new_settings
        with patch.object(nova_navigator_module, "SettingsDialog", MagicMock(return_value=dialog)):
            worker = screen._action_settings()
            await worker.wait()
            await pilot.pause()
        _assert_hidden(screen, True)

    async with _session(config_dir) as (_app, _pilot, screen):
        _assert_hidden(screen, True)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_toggle_keeps_hand_edits(config_dir: Path, settings_file: Path) -> None:
    async with _session(config_dir) as (_app, pilot, screen):
        settings_file.write_text("# hand note\n[general]\nshow_hidden_files = false\nconfirm_delete = false\n")
        await pilot.press("ctrl+h")
        await pilot.pause()
        _assert_hidden(screen, True)

    text = settings_file.read_text()
    assert "# hand note" in text
    assert "confirm_delete = false" in text
    assert "show_hidden_files = true" in text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_non_boolean_setting_counts_as_off(config_dir: Path, settings_file: Path) -> None:
    settings_file.write_text('[general]\nshow_hidden_files = "yes"\n')
    async with _session(config_dir) as (_app, _pilot, screen):
        _assert_hidden(screen, False)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("settings_file")
async def test_unwritable_settings_does_not_break_toggle(config_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def _raise(_self: Settings) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(Settings, "save", _raise)
    caplog.set_level(logging.WARNING)
    async with _session(config_dir) as (app, pilot, screen):
        await pilot.press("ctrl+h")
        await pilot.pause()
        assert app.is_running
        _assert_hidden(screen, True)
    assert any("show_hidden_files" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_damaged_settings_is_not_overwritten_by_toggle(config_dir: Path, settings_file: Path) -> None:
    async with _session(config_dir) as (_app, pilot, screen):
        settings_file.write_text("[general")
        before = settings_file.read_bytes()
        await pilot.press("ctrl+h")
        await pilot.pause()
        _assert_hidden(screen, True)
        assert settings_file.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.integration
async def test_toggle_with_empty_settings_file_stores_key(config_dir: Path, settings_file: Path) -> None:
    settings_file.write_text("")
    async with _session(config_dir) as (_app, pilot, screen):
        await pilot.press("ctrl+h")
        await pilot.pause()
        _assert_hidden(screen, True)
    assert Settings.load().general.show_hidden_files is True
