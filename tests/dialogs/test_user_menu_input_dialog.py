from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from nova_navigator.dialogs.user_menu_input_dialog import InputField, UserMenuInputDialog
from nova_navigator.response import Response

_FIELDS = [InputField("archive", "Archive name", "p.tar.gz"), InputField("level", "Level", "6")]


class _DialogApp(App[None]):
    def __init__(self, dialog: UserMenuInputDialog) -> None:
        super().__init__()
        self._dialog = dialog
        self.response: Response | None = None
        self.finished = False

    def compose(self) -> ComposeResult:
        return iter([])

    def on_mount(self) -> None:
        self.run_worker(self._show())

    async def _show(self) -> None:
        self.response = await self._dialog.run()
        self.finished = True


@pytest.mark.asyncio
async def test_shows_one_input_per_field_with_defaults() -> None:
    dialog = UserMenuInputDialog("Compress", _FIELDS)
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        values = [i.value for i in app.screen.query(Input)]
        assert values == ["p.tar.gz", "6"]


@pytest.mark.asyncio
async def test_enter_accepts_and_returns_edited_values() -> None:
    dialog = UserMenuInputDialog("Compress", _FIELDS)
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.screen.query(Input).first()
        first.value = "out.tgz"
        await pilot.press("enter")
        await pilot.pause()
        assert app.response == Response.OK
        assert dialog.values == {"archive": "out.tgz", "level": "6"}


@pytest.mark.asyncio
async def test_escape_cancels() -> None:
    dialog = UserMenuInputDialog("Compress", _FIELDS)
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.finished
        assert app.response == Response.CANCEL
