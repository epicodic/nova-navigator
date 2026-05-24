from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from nova_widgets.keymap.format import KeyDisplayStyle
from nova_widgets.keymap.hint_bar import HintBar
from nova_widgets.menu._action import Action


class _HintBarTestApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self._hint_bar = HintBar()

    def compose(self) -> ComposeResult:
        yield self._hint_bar

    @property
    def hint_bar(self) -> HintBar:
        return self._hint_bar


@pytest.mark.asyncio
async def test_hint_bar_mounts_without_error() -> None:
    app = _HintBarTestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query(HintBar).first() is not None


@pytest.mark.asyncio
async def test_hint_bar_shows_normal_hints() -> None:
    actions = [
        Action("Copy", name="browser.copy", default_key="f5", show_in_bar=True, bar_priority=10),
        Action("Quit", name="app.quit", default_key="ctrl+q", show_in_bar=True, bar_priority=20),
    ]
    actions[0].set_shortcut("f5")
    actions[1].set_shortcut("ctrl+q")

    app = _HintBarTestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.hint_bar.set_hints(actions, KeyDisplayStyle.CLASSIC)
        await pilot.pause()
        rendered = app.hint_bar.render()
        content = str(rendered)
        assert "Copy" in content or "F5" in content


@pytest.mark.asyncio
async def test_hint_bar_shows_chord_continuations() -> None:
    app = _HintBarTestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.hint_bar.show_chord_pending("ctrl+x", [("ctrl+s", "app.settings")])
        await pilot.pause()
        assert app.hint_bar.is_chord_pending is True


@pytest.mark.asyncio
async def test_hint_bar_clears_chord_on_reset() -> None:
    app = _HintBarTestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.hint_bar.show_chord_pending("ctrl+x", [("ctrl+s", "app.settings")])
        await pilot.pause()
        app.hint_bar.clear_chord()
        await pilot.pause()
        assert app.hint_bar.is_chord_pending is False
