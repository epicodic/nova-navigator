import pytest
from textual.app import App, ComposeResult
from textual.screen import Screen

from nova_widgets.animated_icon import AnimatedIcon
from nova_widgets.icon import Icon


class _TestApp(App[None]):
    def __init__(self, widget: AnimatedIcon) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


@pytest.mark.asyncio
async def test_animated_icon_renders_static_glyph() -> None:
    icon = AnimatedIcon(Icon.of("A"))
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert icon.renderable == Icon.of("A").markup


@pytest.mark.asyncio
async def test_animated_icon_set_glyph_updates_display() -> None:
    icon = AnimatedIcon(Icon.of("A"))
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon.icon_static(Icon.of("B"))
        await pilot.pause()
        assert icon.renderable == Icon.of("B").markup


@pytest.mark.asyncio
async def test_animated_icon_stop_animation_restores_static_glyph() -> None:
    icon = AnimatedIcon(Icon.of("A"))
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon.icon_animate(Icon.from_glyphs(["X", "Y"]), interval=0.05)
        await pilot.pause(delay=0.15)
        icon.stop_icon_animation()
        await pilot.pause()
        assert icon.renderable == Icon.of("A").markup


@pytest.mark.asyncio
async def test_animated_icon_click_calls_action() -> None:
    triggered: list[str] = []
    icon = AnimatedIcon(Icon.of("A"), action="test_action")

    class _ActionScreen(Screen[None]):
        def compose(self) -> ComposeResult:
            yield icon

        def action_test_action(self) -> None:
            triggered.append("test_action")

    class _ActionApp(App[None]):
        def on_mount(self) -> None:
            self.push_screen(_ActionScreen())

    app = _ActionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.hover(icon)
        await pilot.click(icon)
        await pilot.pause(delay=0.1)
        assert triggered == ["test_action"]


@pytest.mark.asyncio
async def test_animated_icon_no_action_click_is_noop() -> None:
    icon = AnimatedIcon(Icon.of("A"), action=None)
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Should not raise
        await pilot.click(icon)
        await pilot.pause()
