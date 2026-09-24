from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.geometry import Offset

from nova_navigator.usermenu import MenuEntry, MenuView
from nova_navigator.usermenu.popup import UserMenuPopup
from nova_widgets.menu import Action

_A = MenuEntry(id="a", label="Alpha", run="a", key="a", group="G1")
_B = MenuEntry(id="b", label="Beta", run="b", key="b", group="G2")
_C = MenuEntry(id="c", label="Gamma", run="c", group="G2")


def _view(default_index: int = 0) -> MenuView:
    return MenuView(rows=(_A, None, _B, _C), default_index=default_index, names={})


class _PopupApp(App[None]):
    def __init__(self, popup: UserMenuPopup) -> None:
        super().__init__()
        self._popup = popup
        self.result: Action | None = None
        self.finished = False

    def compose(self) -> ComposeResult:
        return iter([])

    def on_mount(self) -> None:
        self.run_worker(self._show())

    async def _show(self) -> None:
        self.result = await self._popup.exec(Offset(0, 0))
        self.finished = True


@pytest.mark.asyncio
async def test_hotkey_selects_entry() -> None:
    app = _PopupApp(UserMenuPopup(_view()))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()
        assert app.finished
        assert app.result is not None
        assert app.result.id == "b"


@pytest.mark.asyncio
async def test_enter_selects_default_entry() -> None:
    app = _PopupApp(UserMenuPopup(_view(default_index=2)))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.result is not None
        assert app.result.id == "b"


@pytest.mark.asyncio
async def test_escape_returns_none() -> None:
    app = _PopupApp(UserMenuPopup(_view()))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.finished
        assert app.result is None


@pytest.mark.asyncio
async def test_unbound_key_does_nothing() -> None:
    app = _PopupApp(UserMenuPopup(_view()))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("z")
        await pilot.pause()
        assert not app.finished


def test_rows_become_actions_with_hotkey_column() -> None:
    popup = UserMenuPopup(_view())
    actions = popup._actions
    assert [a.is_separator for a in actions] == [False, True, False, False]
    assert actions[0].text == "a  Alpha"
    assert actions[3].text == "   Gamma"
    assert actions[2].id == "b"
