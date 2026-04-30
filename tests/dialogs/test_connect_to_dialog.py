# tests/dialogs/test_connect_to_dialog.py
from __future__ import annotations

import pathlib

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Label, ListItem, ListView

from nova_navigator.config.remotes import RemoteConfig, RemoteConnection, SshSettings
from nova_navigator.dialogs.connect_to_dialog import ConnectToDialog, _RemoteListView


@pytest.fixture(autouse=True, scope="module")
def _load_icons() -> None:
    """Ensure the icon registry is initialised before any test runs."""
    from nova_navigator.icons import ICONS

    if not hasattr(ICONS, "_icons"):
        icons_csv = pathlib.Path(__file__).parent.parent.parent / "src" / "nova_navigator" / "_default" / "icons.csv"
        ICONS.load_icons(icons_csv)


def _make_config(*connections: RemoteConnection) -> RemoteConfig:
    cfg = object.__new__(RemoteConfig)
    cfg._items = list(connections)
    return cfg


def _make_app(dialog: ConnectToDialog) -> type[App[None]]:
    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    return _App


@pytest.mark.asyncio
async def test_empty_remotes_shows_label() -> None:
    cfg = _make_config()
    dialog = ConnectToDialog(cfg)
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        labels = list(app.screen.query(Label))
        texts = [str(lbl.content) for lbl in labels]
        assert any("No remotes configured" in t for t in texts)


@pytest.mark.asyncio
async def test_empty_remotes_ok_button_disabled() -> None:
    cfg = _make_config()
    dialog = ConnectToDialog(cfg)
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        ok_btn = app.screen.query_one("#OK", Button)
        assert ok_btn.disabled


@pytest.mark.asyncio
async def test_single_remote_selected_on_ok() -> None:
    conn = RemoteConnection(name="my-server", ssh=SshSettings(host="1.2.3.4"))
    cfg = _make_config(conn)
    dialog = ConnectToDialog(cfg)
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert dialog.selected_connection is conn


@pytest.mark.asyncio
async def test_cancel_returns_none() -> None:
    conn = RemoteConnection(name="my-server", ssh=SshSettings(host="1.2.3.4"))
    cfg = _make_config(conn)
    dialog = ConnectToDialog(cfg)
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert dialog.selected_connection is None


@pytest.mark.asyncio
async def test_double_click_accepts() -> None:
    conn = RemoteConnection(name="my-server", ssh=SshSettings(host="1.2.3.4"))
    cfg = _make_config(conn)
    dialog = ConnectToDialog(cfg)
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        lv = app.screen.query_one("#remote_list", _RemoteListView)
        item = lv.query(ListItem).first()
        # Pilot click() events do not carry chain==2, so simulate double-click by
        # setting the internal flags directly and posting a ListView.Selected message.
        lv._click_is_double = True
        lv._last_event_was_click = True
        lv.post_message(ListView.Selected(lv, item, 0))
        await pilot.pause()
        assert dialog.selected_connection is conn


@pytest.mark.asyncio
async def test_ok_button_click_sets_connection() -> None:
    conn = RemoteConnection(name="my-server", ssh=SshSettings(host="1.2.3.4"))
    cfg = _make_config(conn)
    dialog = ConnectToDialog(cfg)
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        ok_btn = app.screen.query_one("#OK", Button)
        await pilot.click(ok_btn)
        await pilot.pause()
        assert dialog.selected_connection is conn


@pytest.mark.asyncio
async def test_second_item_accepted() -> None:
    conn1 = RemoteConnection(name="server-1", ssh=SshSettings(host="1.2.3.4"))
    conn2 = RemoteConnection(name="server-2", ssh=SshSettings(host="5.6.7.8"))
    cfg = _make_config(conn1, conn2)
    dialog = ConnectToDialog(cfg)
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert dialog.selected_connection is conn2
