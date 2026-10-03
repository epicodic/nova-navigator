"""Characterisation of the ``Dialog`` base class: accept, reject and custom buttons."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from nova_widgets import ButtonSpec, DefaultButton, Dialog, Response


class _HostApp(App[None]):
    def __init__(self, dialog: Dialog) -> None:
        super().__init__()
        self._dialog = dialog
        self.results: list[Response | None] = []

    def compose(self) -> ComposeResult:
        return iter([])

    async def on_mount(self) -> None:
        await self.push_screen(self._dialog, callback=self.results.append)


@pytest.mark.asyncio
async def test_enter_accepts_with_default_ok_button() -> None:
    app = _HostApp(Dialog("Title"))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.results == [Response.OK]


@pytest.mark.asyncio
async def test_enter_accepts_with_accepting_button() -> None:
    app = _HostApp(Dialog("Title", buttons=[DefaultButton.YES, DefaultButton.NO]))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.results == [Response.YES]


@pytest.mark.asyncio
async def test_escape_dismisses_with_reject_response() -> None:
    app = _HostApp(Dialog("Title", buttons=[DefaultButton.YES, DefaultButton.NO]))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.results == [Response.NO]


@pytest.mark.asyncio
async def test_escape_dismisses_with_none_when_no_reject_button() -> None:
    app = _HostApp(Dialog("Title"))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.results == [None]


@pytest.mark.asyncio
async def test_enter_on_focused_reject_button_rejects() -> None:
    app = _HostApp(Dialog("Title", buttons=[DefaultButton.OK, DefaultButton.CANCEL]))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.screen.query_one(f"#{Response.CANCEL.name}", Button).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.results == [Response.CANCEL]


@pytest.mark.asyncio
async def test_custom_button_spec_label_and_variant() -> None:
    dialog = Dialog(
        "Title",
        buttons=[
            ButtonSpec(Response.OK, label="Save", variant="success"),
            ButtonSpec(Response.CANCEL, label="Discard"),
        ],
    )
    app = _HostApp(dialog)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        save = app.screen.query_one(f"#{Response.OK.name}", Button)
        discard = app.screen.query_one(f"#{Response.CANCEL.name}", Button)
        assert str(save.label) == "Save"
        assert save.variant == "success"
        assert str(discard.label) == "Discard"
        assert discard.variant == "error"
        await pilot.click(save)
        await pilot.pause()
        assert app.results == [Response.OK]


def test_button_spec_defaults_follow_response_role() -> None:
    assert ButtonSpec(Response.OK).display_variant == "primary"
    assert ButtonSpec(Response.NO).display_variant == "error"
    assert ButtonSpec(Response.OK).display_label == Response.OK.tr
