from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from nova_widgets import ButtonSpec, MessageDialog, Response
from nova_widgets.flat_widgets import Button


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


def _four_buttons() -> list[ButtonSpec | Response]:
    return [
        ButtonSpec(Response.CANCEL, "Keep", "default"),
        ButtonSpec(Response.DISCARD, "Discard & Reload", "error"),
        ButtonSpec(Response.SAVE, "Save As…", "primary"),
        ButtonSpec(Response.OVERWRITE, "Overwrite", "warning"),
    ]


@pytest.mark.asyncio
async def test_the_default_width_is_unchanged() -> None:
    dialog = MessageDialog("Hello", title="T")
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.screen.query_one("#dialog_box").region.width == 40


@pytest.mark.asyncio
async def test_a_given_width_is_applied_to_the_box() -> None:
    dialog = MessageDialog("Hello", title="T", width="90%")
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.screen.query_one("#dialog_box").region.width == 72


@pytest.mark.asyncio
async def test_four_buttons_fit_a_wide_box_at_80_columns() -> None:
    dialog = MessageDialog("Hello", title="T", buttons=_four_buttons(), width="90%")
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen_region = app.screen.region
        buttons = list(app.screen.query(Button))
        assert len(buttons) == 4
        assert all(screen_region.contains_region(button.region) for button in buttons)
