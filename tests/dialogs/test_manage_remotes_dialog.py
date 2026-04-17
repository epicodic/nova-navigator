# tests/dialogs/test_manage_remotes_dialog.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Checkbox, Input, ListItem, ListView, Static

from nova_navigator.config.remotes import RemoteConfig, RemoteConnection, SshSettings
from nova_navigator.dialogs.edit_remotes_dialog import EditRemotesDialog


def _fixture_config() -> RemoteConfig:
    cfg = object.__new__(RemoteConfig)
    cfg._items = [
        RemoteConnection(
            name="my-server",
            uri="ssh://alice@192.168.1.10",
            ssh=SshSettings(host="192.168.1.10", user="alice", port=None, identity_file=None),
        ),
        RemoteConnection(
            name="dev-box",
            uri="ssh://dev.example.com",
            ssh=SshSettings(host="dev.example.com"),
        ),
    ]
    return cfg


def _make_app(cfg: RemoteConfig) -> tuple[EditRemotesDialog, type[App[None]]]:
    dialog = EditRemotesDialog(cfg)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    _App.run_test = lambda self, **kw: App.run_test(self, size=kw.pop("size", (120, 50)), **kw)  # type: ignore[method-assign]

    return dialog, _App


@pytest.mark.asyncio
async def test_list_shows_configured_remotes() -> None:
    cfg = _fixture_config()
    _dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        lv = app.screen.query_one("#remote_list", ListView)
        items = list(lv.query(ListItem))
        assert len(items) == 2


@pytest.mark.asyncio
async def test_add_button_appends_and_selects() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click(app.screen.query_one("#btn_add", Button))
        await pilot.pause()
        assert len(dialog._working) == 3
        assert dialog._working[-1].name == "new-connection"
        lv = app.screen.query_one("#remote_list", ListView)
        assert lv.index == 2


@pytest.mark.asyncio
async def test_remove_button_removes_selected() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        # index 0 (my-server) is already selected on mount
        await pilot.click(app.screen.query_one("#btn_remove", Button))
        await pilot.pause()
        assert len(dialog._working) == 1
        assert dialog._working[0].name == "dev-box"


@pytest.mark.asyncio
async def test_form_fields_populate_on_selection() -> None:
    cfg = _fixture_config()
    _dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.1)
        assert app.screen.query_one("#input_name", Input).value == "my-server"
        assert app.screen.query_one("#input_address", Input).value == "192.168.1.10"
        assert app.screen.query_one("#input_username", Input).value == "alice"


@pytest.mark.asyncio
async def test_uri_preview_updates_on_address_change() -> None:
    cfg = _fixture_config()
    _dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.1)
        addr_input = app.screen.query_one("#input_address", Input)
        addr_input.value = "10.0.0.1"
        await pilot.pause()
        preview = app.screen.query_one("#uri_preview", Static)
        assert "10.0.0.1" in str(preview.content)


@pytest.mark.asyncio
async def test_proxy_fields_hidden_when_unchecked() -> None:
    cfg = _fixture_config()
    _dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.1)
        # first entry has no proxy — host/port inputs should be disabled
        assert app.screen.query_one("#input_proxy_host", Input).disabled


@pytest.mark.asyncio
async def test_proxy_fields_shown_when_checked() -> None:
    cfg = _fixture_config()
    _dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.1)
        checkbox = app.screen.query_one("#check_proxy", Checkbox)
        checkbox.value = True
        await pilot.pause()
        assert not app.screen.query_one("#input_proxy_host", Input).disabled


@pytest.mark.asyncio
async def test_ok_saves_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    cfg = _fixture_config()
    _dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.1)
        name_input = app.screen.query_one("#input_name", Input)
        name_input.value = "renamed-server"
        await pilot.pause()
        with patch.object(cfg, "save"):
            await pilot.press("enter")
            await pilot.pause()
    assert cfg._items[0].name == "renamed-server"


@pytest.mark.asyncio
async def test_cancel_discards_changes() -> None:
    cfg = _fixture_config()
    original_name = cfg._items[0].name
    _dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.1)
        name_input = app.screen.query_one("#input_name", Input)
        name_input.value = "should-not-persist"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert cfg._items[0].name == original_name
