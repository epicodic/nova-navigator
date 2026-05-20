"""Tests for DirectoryBrowser.set_item_colors() and LoadComplete message."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest
from rich.style import Style

from nova_navigator.vfs.vpath import VPath
from nova_navigator.widgets.directory_browser import DirectoryBrowser, UpPath
from tests.widgets._directory_browser_fixtures import flat_dir_fs, run_browser


@pytest.mark.asyncio
async def test_set_item_colors_stores_dict() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        browser.set_item_colors({"file_a.txt": "yellow"})
        assert browser._item_colors == {"file_a.txt": "yellow"}


@pytest.mark.asyncio
async def test_set_item_colors_none_clears_dict() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        browser.set_item_colors({"file_a.txt": "yellow"})
        browser.set_item_colors(None)
        assert browser._item_colors == {}


@pytest.mark.asyncio
async def test_highlight_style_includes_item_color_for_matching_filename() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        browser.set_item_colors({"file_a.txt": "yellow"})

        fs = flat_dir_fs()
        vp = VPath(PurePosixPath("/home/user/file_a.txt"), fs)
        vp._stat = fs.stat(vp)  # type: ignore[attr-defined]

        styles = browser._highlight_style(vp)
        assert Style(color="yellow") in styles


@pytest.mark.asyncio
async def test_highlight_style_excludes_item_color_for_non_matching_filename() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        browser.set_item_colors({"file_a.txt": "yellow"})

        fs = flat_dir_fs()
        vp = VPath(PurePosixPath("/home/user/file_b.py"), fs)
        vp._stat = fs.stat(vp)  # type: ignore[attr-defined]

        styles = browser._highlight_style(vp)
        assert Style(color="yellow") not in styles


@pytest.mark.asyncio
async def test_load_complete_message_fires_after_directory_loads() -> None:
    from textual.app import App, ComposeResult

    load_complete_msgs: list[DirectoryBrowser.LoadComplete] = []

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            fs = flat_dir_fs()
            vp = VPath(PurePosixPath("/home/user"), fs)
            yield DirectoryBrowser(vp)

        def on_directory_browser_load_complete(self, msg: DirectoryBrowser.LoadComplete) -> None:
            load_complete_msgs.append(msg)

    async with _App().run_test() as pilot:
        await pilot.pause()
        assert len(load_complete_msgs) >= 1
        msg = load_complete_msgs[-1]
        assert isinstance(msg.path, VPath)
        assert any(vp.name == "file_a.txt" for vp in msg.browser.items)


@pytest.mark.asyncio
async def test_load_complete_items_excludes_up_path() -> None:
    """LoadComplete.items must not contain UpPath entries."""
    from textual.app import App, ComposeResult

    load_complete_msgs: list[DirectoryBrowser.LoadComplete] = []

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            fs = flat_dir_fs()
            vp = VPath(PurePosixPath("/home/user"), fs)
            yield DirectoryBrowser(vp)

        def on_directory_browser_load_complete(self, msg: DirectoryBrowser.LoadComplete) -> None:
            load_complete_msgs.append(msg)

    async with _App().run_test() as pilot:
        await pilot.pause()
        assert load_complete_msgs
        assert not any(isinstance(vp, UpPath) for vp in load_complete_msgs[-1].browser.items)
