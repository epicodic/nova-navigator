from __future__ import annotations

from typing import ClassVar

import pytest
from textual.app import App, ComposeResult
from textual.geometry import Region
from textual.widgets import Button

from nova_widgets import PopupWidget


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
        # Slot " 🗙 " is 3 cells wide, placed before pad+corner (2 cells)
        # So positions w-5..w-3 are space, glyph, space; top_text[w-4] == "🗙"
        assert top_text[w - 4] == "🗙"


class FocusableOverlay(PopupWidget, can_focus=True):
    """OverlayWidget subclass with focus enabled, for keyboard binding tests."""


class RemovePopup(PopupWidget):
    CLOSE_ACTION = PopupWidget.CloseAction.REMOVE


class KeepPopup(PopupWidget):
    CLOSE_ACTION = PopupWidget.CloseAction.KEEP


class NoEscapeOverlay(PopupWidget, can_focus=True):
    BINDINGS: ClassVar[list] = []

    def action_close_popup(self) -> None:
        return


class OverlayTestApp(App[None]):
    CSS = """
    Screen {
        layers: base above;
    }
    """

    def __init__(self, overlay: PopupWidget) -> None:
        super().__init__()
        self._overlay = overlay

    def compose(self) -> ComposeResult:
        yield Button("Background", id="bg")
        yield self._overlay

    def on_mount(self) -> None:
        self._overlay.focus()


@pytest.mark.asyncio
async def test_overlay_mounts_with_correct_title() -> None:
    overlay = PopupWidget("My Title", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert overlay.border_title == "My Title"


@pytest.mark.asyncio
async def test_overlay_mounts_with_correct_position() -> None:
    overlay = PopupWidget("Test", (5, 10))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert overlay.offset == (5, 10)


@pytest.mark.asyncio
async def test_show_makes_overlay_visible() -> None:
    overlay = PopupWidget("Test", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        overlay.display = False
        overlay.show()
        await pilot.pause()
        assert overlay.display is True


@pytest.mark.asyncio
async def test_hide_makes_overlay_invisible() -> None:
    overlay = PopupWidget("Test", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        overlay.hide()
        await pilot.pause()
        assert overlay.display is False


@pytest.mark.asyncio
async def test_close_with_hide_action_hides_overlay() -> None:
    overlay = PopupWidget("Test", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        overlay.close()
        await pilot.pause()
        assert overlay.display is False
        assert len(app.query(PopupWidget)) == 1


@pytest.mark.asyncio
async def test_close_with_remove_action_removes_overlay_from_dom() -> None:
    overlay = RemovePopup("Test", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        overlay.close()
        await pilot.pause()
        assert len(app.query(PopupWidget)) == 0


@pytest.mark.asyncio
async def test_close_with_none_action_leaves_overlay_visible() -> None:
    overlay = KeepPopup("Test", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        overlay.close()
        await pilot.pause()
        assert overlay.display is True
        assert len(app.query(PopupWidget)) == 1


@pytest.mark.asyncio
async def test_escape_key_closes_overlay_when_close_on_escape_true() -> None:
    overlay = FocusableOverlay("Test", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert overlay.display is False


@pytest.mark.asyncio
async def test_escape_key_does_nothing_when_close_on_escape_false() -> None:
    overlay = NoEscapeOverlay("Test", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert overlay.display is True


@pytest.mark.asyncio
async def test_close_restores_focus_to_saved_widget() -> None:
    overlay = PopupWidget("Test", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        bg = app.query_one("#bg", Button)
        bg.focus()
        await pilot.pause()
        overlay.focus()
        await pilot.pause()
        overlay.close()
        await pilot.pause()
        assert app.focused is bg
