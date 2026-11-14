"""The flat Checkbox draws its box with the same symbols as checkable menu items."""

import pytest
from textual.app import App, ComposeResult

from nova_widgets import Checkbox, Icon
from nova_widgets.menu import SYMBOL_TABLE


class _CheckboxApp(App[None]):
    def compose(self) -> ComposeResult:
        yield Checkbox("Checked", True, id="on")
        yield Checkbox("Unchecked", False, id="off")


def _unchecked() -> str:
    return SYMBOL_TABLE["checkbox"][0].glyph.rstrip()


def _checked() -> str:
    return SYMBOL_TABLE["checkbox"][1].glyph.rstrip()


@pytest.mark.asyncio
async def test_checked_box_uses_menu_checked_symbol() -> None:
    app = _CheckboxApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#on", Checkbox)._button.plain == _checked()


@pytest.mark.asyncio
async def test_unchecked_box_uses_menu_unchecked_symbol() -> None:
    app = _CheckboxApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#off", Checkbox)._button.plain == _unchecked()


@pytest.mark.asyncio
async def test_toggling_updates_the_box() -> None:
    app = _CheckboxApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        checkbox = app.query_one("#off", Checkbox)
        checkbox.focus()
        await pilot.press("space")
        await pilot.pause()
        assert checkbox.value is True
        assert checkbox._button.plain == _checked()


@pytest.mark.asyncio
async def test_symbols_overridden_by_the_app_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(SYMBOL_TABLE, "checkbox", (Icon.of("o"), Icon.of("*")))
    app = _CheckboxApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#on", Checkbox)._button.plain == "*"
        assert app.query_one("#off", Checkbox)._button.plain == "o"
