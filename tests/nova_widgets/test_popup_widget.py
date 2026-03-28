from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.geometry import Region

from nova_navigator.widgets.popup_widget import PopupWidget


class _TestPopup(PopupWidget):
    SHOW_CLOSE_BUTTON = True

    def __init__(self) -> None:
        super().__init__(title="Test", position=(0, 0))


class _PopupTestApp(App[None]):
    def __init__(self, popup: PopupWidget) -> None:
        super().__init__()
        self._popup = popup

    def compose(self) -> ComposeResult:
        yield self._popup


@pytest.mark.asyncio
async def test_close_button_absent_when_show_close_button_false() -> None:
    class _NoBtn(PopupWidget):
        SHOW_CLOSE_BUTTON = False

        def __init__(self) -> None:
            super().__init__(title="X", position=(0, 0))

    popup = _NoBtn()
    async with _PopupTestApp(popup).run_test() as pilot:
        await pilot.pause()
        w = popup.outer_size.width
        strips = popup.render_lines(Region(0, 0, w, popup.outer_size.height))
        top_text = strips[0].text
        assert "🗙" not in top_text


@pytest.mark.asyncio
async def test_close_button_present_when_show_close_button_true() -> None:
    popup = _TestPopup()
    async with _PopupTestApp(popup).run_test() as pilot:
        await pilot.pause()
        w = popup.outer_size.width
        strips = popup.render_lines(Region(0, 0, w, popup.outer_size.height))
        top_text = strips[0].text
        assert "🗙" in top_text


@pytest.mark.asyncio
async def test_close_button_at_top_right() -> None:
    popup = _TestPopup()
    async with _PopupTestApp(popup).run_test() as pilot:
        await pilot.pause()
        w = popup.outer_size.width
        strips = popup.render_lines(Region(0, 0, w, popup.outer_size.height))
        top_text = strips[0].text
        # Slot " 🗙 " is 3 cells wide, placed before the right corner (1 cell)
        # So positions w-4..w-2 are space, glyph, space; top_text[w-3] == "🗙"
        assert top_text[w - 3] == "🗙"
