from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from nova_navigator.keymap.context import NovaContextResolver
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.widgets.directory_browser import DirectoryBrowser


class _ContextTestApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self._browser = DirectoryBrowser(id="pane-left", path=LocalFilesystem.singleton().cwd())

    def compose(self) -> ComposeResult:
        yield self._browser

    @property
    def browser(self) -> DirectoryBrowser:
        return self._browser


@pytest.mark.asyncio
async def test_context_browser_no_selection() -> None:
    app = _ContextTestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.browser.focus()
        await pilot.pause()
        resolver = NovaContextResolver(app)
        assert resolver.resolve() == "browser"


@pytest.mark.asyncio
async def test_context_hover_returns_none_when_no_hover() -> None:
    app = _ContextTestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        resolver = NovaContextResolver(app)
        assert resolver.hover_context() is None
