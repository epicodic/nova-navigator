from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from nova_navigator.dialogs.message_box import MessageDialog
from nova_navigator.response import Response


def _make_app(dialog: MessageDialog) -> type[App[None]]:
    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    return _App


@pytest.mark.asyncio
async def test_renders_message() -> None:
    dialog = MessageDialog("Something went wrong")
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        labels = list(app.screen.query(Label))
        texts = [str(lbl.content) for lbl in labels]
        assert any("Something went wrong" in t for t in texts)


@pytest.mark.asyncio
async def test_ok_dismisses() -> None:
    dismissed: list[Response | None] = []

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(MessageDialog("Error occurred"), callback=dismissed.append)

    app = _App()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert dismissed == [Response.OK]
