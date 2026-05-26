from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from nova_widgets.action import Action
from nova_widgets.keymap.hint_bar import HintBar
from nova_widgets.keymap.key_sequence import KeyFormatStyle, KeySequence


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
        Action("Copy", name="browser.copy", shortcut="f5", show=True, bar_priority=10),
        Action("Quit", name="app.quit", shortcut="ctrl+q", show=True, bar_priority=20),
    ]
    actions[0].set_shortcut("f5")
    actions[1].set_shortcut("ctrl+q")

    app = _HintBarTestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.hint_bar.set_hints(actions, KeyFormatStyle.CLASSIC)
        await pilot.pause()
        rendered = app.hint_bar.render()
        content = str(rendered)
        assert "Copy" in content or "F5" in content


@pytest.mark.asyncio
async def test_hint_bar_shows_chord_continuations() -> None:
    cont = Action("Settings", name="app.settings", shortcut="ctrl+s", show=True)
    cont.set_shortcut("ctrl+s")
    app = _HintBarTestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.hint_bar.show_chord_pending(KeySequence.parse("ctrl+x"), [cont])
        await pilot.pause()
        app.hint_bar.clear_chord()
        await pilot.pause()
        assert app.hint_bar.is_chord_pending is False
