import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from nova_navigator.widgets.overlay_widget import OverlayWidget


class FocusableOverlay(OverlayWidget, can_focus=True):
    """OverlayWidget subclass with focus enabled, for keyboard binding tests."""


class OverlayTestApp(App[None]):
    CSS = """
    Screen {
        layers: base above;
    }
    """

    def __init__(self, overlay: OverlayWidget) -> None:
        super().__init__()
        self._overlay = overlay

    def compose(self) -> ComposeResult:
        yield Button("Background", id="bg")
        yield self._overlay

    def on_mount(self) -> None:
        self._overlay.focus()


@pytest.mark.asyncio
async def test_overlay_mounts_with_correct_title() -> None:
    overlay = OverlayWidget("My Title", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert overlay.border_title == "My Title"


@pytest.mark.asyncio
async def test_overlay_mounts_with_correct_position() -> None:
    overlay = OverlayWidget("Test", (5, 10))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert overlay.offset == (5, 10)


@pytest.mark.asyncio
async def test_show_makes_overlay_visible() -> None:
    overlay = OverlayWidget("Test", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        overlay.display = False
        overlay.show()
        await pilot.pause()
        assert overlay.display is True


@pytest.mark.asyncio
async def test_hide_makes_overlay_invisible() -> None:
    overlay = OverlayWidget("Test", (0, 0))
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        overlay.hide()
        await pilot.pause()
        assert overlay.display is False


@pytest.mark.asyncio
async def test_close_with_hide_action_hides_overlay() -> None:
    overlay = OverlayWidget("Test", (0, 0), close_action=OverlayWidget.CloseAction.HIDE)
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        overlay.close()
        await pilot.pause()
        assert overlay.display is False
        assert len(app.query(OverlayWidget)) == 1


@pytest.mark.asyncio
async def test_close_with_remove_action_removes_overlay_from_dom() -> None:
    overlay = OverlayWidget("Test", (0, 0), close_action=OverlayWidget.CloseAction.REMOVE)
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        overlay.close()
        await pilot.pause()
        assert len(app.query(OverlayWidget)) == 0


@pytest.mark.asyncio
async def test_close_with_none_action_leaves_overlay_visible() -> None:
    overlay = OverlayWidget("Test", (0, 0), close_action=OverlayWidget.CloseAction.NONE)
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        overlay.close()
        await pilot.pause()
        assert overlay.display is True
        assert len(app.query(OverlayWidget)) == 1


@pytest.mark.asyncio
async def test_q_key_closes_overlay_when_close_on_escape_true() -> None:
    overlay = FocusableOverlay("Test", (0, 0), close_on_escape=True, close_action=OverlayWidget.CloseAction.HIDE)
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert overlay.display is False


@pytest.mark.asyncio
async def test_q_key_does_nothing_when_close_on_escape_false() -> None:
    overlay = FocusableOverlay("Test", (0, 0), close_on_escape=False, close_action=OverlayWidget.CloseAction.HIDE)
    app = OverlayTestApp(overlay)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert overlay.display is True


@pytest.mark.asyncio
async def test_close_restores_focus_to_saved_widget() -> None:
    overlay = OverlayWidget("Test", (0, 0), close_action=OverlayWidget.CloseAction.HIDE)
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
