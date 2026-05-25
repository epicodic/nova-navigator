from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

import pytest
from textual.app import App, ComposeResult
from textual.dom import DOMNode
from textual.widget import Widget

from nova_widgets.keymap.hint_bar import HintBar
from nova_widgets.keymap.registry import KeymapRegistry
from nova_widgets.menu._action import Action


class _BrowserWidget(Widget):
    ACTIONS: ClassVar[list[Action]] = [
        Action("Copy", name="browser.copy", action="copy", key="f5"),
        Action("Filter", name="browser.filter", action="filter", key="ctrl+f"),
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
    registry.reload({"browser.copy": "f5", "browser.filter": "ctrl+f"})

    app = _RegistryTestApp(registry)
    async with app.run_test() as pilot:
        await pilot.pause()
        actions = registry.collect_actions(app)
        names = [a.name for a in actions]
        assert "browser.copy" in names
        assert "browser.filter" in names


@pytest.mark.asyncio
async def test_registry_handle_key_dispatches_action() -> None:
    registry = KeymapRegistry(HintBar())
    registry.reload({"browser.copy": "f5"})

    app = _RegistryTestApp(registry)
    async with app.run_test() as pilot:
        await pilot.pause()
        consumed = await registry.handle_key("f5", app)
        assert consumed is True
        assert "copy" in app.dispatched


@pytest.mark.asyncio
async def test_registry_unknown_key_not_consumed() -> None:
    registry = KeymapRegistry(HintBar())
    registry.reload({"browser.copy": "f5"})

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
        Action("Select", name="widget.select", action="select", key="insert"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.widget_action_called = False

    def action_select(self) -> None:
        self.widget_action_called = True


class _RealDispatchScreen(Widget):
    """Screen-equivalent widget with a screen-level action."""

    ACTIONS: ClassVar[list[Action]] = [
        Action("Copy", name="screen.copy", action="copy_files", key="f5"),
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
        Action("Select", name="widget.select", action="select", key="insert"),
    ]
    registry.reload({"widget.select": "insert"}, actions)

    app = _RealDispatchApp(registry)
    async with app.run_test() as pilot:
        await pilot.pause()
        consumed = await registry.handle_key("insert", app)
        assert consumed is True
        assert app._focused_widget.widget_action_called is True
