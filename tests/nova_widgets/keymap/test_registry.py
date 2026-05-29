from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

import pytest
from textual.app import App, ComposeResult
from textual.dom import DOMNode
from textual.widget import Widget

from nova_widgets.action import Action
from nova_widgets.keymap.hint_bar import HintBar
from nova_widgets.keymap.key_sequence import KeySequence
from nova_widgets.keymap.registry import KeymapRegistry


class _BrowserWidget(Widget):
    ACTIONS: ClassVar[list[Action]] = [
        Action("Copy", id="browser.copy", action="copy", shortcut="f5"),
        Action("Filter", id="browser.filter", action="filter", shortcut="ctrl+f"),
    ]


class _RegistryTestApp(App[object]):
    def __init__(self, registry: KeymapRegistry) -> None:
        super().__init__()
        self._km_registry = registry
        self._browser = _BrowserWidget()
        self.dispatched: list[str] = []

    def compose(self) -> ComposeResult:
        yield self._browser

    async def on_mount(self) -> None:
        self._browser.focus()

    async def run_action(
        self,
        action: str | tuple[str, str, tuple[object, ...]],
        default_namespace: DOMNode | None = None,
        namespaces: Mapping[str, DOMNode] | None = None,
    ) -> bool:
        if isinstance(action, str):
            self.dispatched.append(action)
        return True


@pytest.mark.asyncio
async def test_registry_collect_actions_from_widget_tree() -> None:
    registry = KeymapRegistry(HintBar())
    registry.reload(
        {
            "browser.copy": KeySequence.parse("f5"),
            "browser.filter": KeySequence.parse("ctrl+f"),
        }
    )

    app = _RegistryTestApp(registry)
    async with app.run_test() as pilot:
        await pilot.pause()
        actions = registry.collect_actions(app)
        names = [a.id for a in actions]
        assert "browser.copy" in names
        assert "browser.filter" in names


@pytest.mark.asyncio
async def test_registry_handle_key_dispatches_action() -> None:
    registry = KeymapRegistry(HintBar())
    registry.reload({"browser.copy": KeySequence.parse("f5")})

    app = _RegistryTestApp(registry)
    async with app.run_test() as pilot:
        await pilot.pause()
        consumed = await registry.handle_key("f5", app)
        assert consumed is True
        assert "copy" in app.dispatched


@pytest.mark.asyncio
async def test_registry_unknown_key_not_consumed() -> None:
    registry = KeymapRegistry(HintBar())
    registry.reload({"browser.copy": KeySequence.parse("f5")})

    app = _RegistryTestApp(registry)
    async with app.run_test() as pilot:
        await pilot.pause()
        consumed = await registry.handle_key("f99", app)
        assert consumed is False


# ---------------------------------------------------------------------------
# Real dispatch tests — verify action dispatch order: focused → screen → app
# ---------------------------------------------------------------------------


class _RealFocusableWidget(Widget, can_focus=True):
    """Widget with a widget-level action (simulates DirectoryBrowser.insert_select)."""

    ACTIONS: ClassVar[list[Action]] = [
        Action("Select", id="widget.select", action="select", shortcut="insert"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.widget_action_called = False

    def action_select(self) -> None:
        self.widget_action_called = True


class _RealDispatchScreen(Widget):
    """Screen-equivalent widget with a screen-level action."""

    ACTIONS: ClassVar[list[Action]] = [
        Action("Copy", id="screen.copy", action="copy_files", shortcut="f5"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.screen_action_called = False

    def action_copy_files(self) -> None:
        self.screen_action_called = True


class _RealDispatchApp(App[None]):
    """Minimal app for testing real action dispatch (no run_action mock)."""

    def __init__(self, registry: KeymapRegistry) -> None:
        super().__init__()
        self._km_registry = registry
        self._focused_widget = _RealFocusableWidget()

    def compose(self) -> ComposeResult:
        yield self._focused_widget

    async def on_mount(self) -> None:
        self._focused_widget.focus()


@pytest.mark.asyncio
async def test_dispatch_widget_action_reaches_focused_widget() -> None:
    """Actions defined on the focused widget are dispatched there, not the screen/app."""
    registry = KeymapRegistry(HintBar())
    # bind insert → widget.select (action lives on the focused widget)
    actions = [
        Action("Select", id="widget.select", action="select", shortcut="insert"),
    ]
    registry.reload({"widget.select": KeySequence.parse("insert")}, actions)

    app = _RealDispatchApp(registry)
    async with app.run_test() as pilot:
        await pilot.pause()
        consumed = await registry.handle_key("insert", app)
        assert consumed is True
        assert app._focused_widget.widget_action_called is True


@pytest.mark.asyncio
async def test_escape_during_pending_sequence_clears_hint_bar() -> None:
    """Pressing Escape while a prefix chord is pending returns the hint bar to normal mode."""
    hint_bar = HintBar()
    registry = KeymapRegistry(hint_bar)
    registry.reload({"app.settings": KeySequence.parse("ctrl+x ctrl+s")})

    app = _RegistryTestApp(registry)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Enter pending state by pressing Ctrl+X (prefix chord)
        consumed = await registry.handle_key("ctrl+x", app)
        assert consumed is True
        assert hint_bar.is_chord_pending is True
        assert registry.pending_chord_info is not None

        # Pressing Escape should cancel the sequence and restore normal mode
        consumed = await registry.handle_key("escape", app)
        assert consumed is False
        assert hint_bar.is_chord_pending is False
        assert registry.pending_chord_info is None
