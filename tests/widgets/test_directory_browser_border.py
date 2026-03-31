from __future__ import annotations

import pytest
from textual.geometry import Region

from nova_navigator.vfs.types import Stat
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem
from tests.widgets._directory_browser_fixtures import run_browser


@pytest.mark.asyncio
async def test_bottom_border_empty_with_no_selection() -> None:
    fs = MockFilesystem(files={"/home/user/a.txt": b"hello"})
    async with run_browser(fs) as (_pilot, browser, _):
        strips = browser.render_lines(Region(0, browser.outer_size.height - 1, browser.outer_size.width, 1))
        bottom_text = strips[0].text
        assert "file" not in bottom_text


@pytest.mark.asyncio
async def test_bottom_border_shows_selected_file_count() -> None:
    fs = MockFilesystem(
        files={
            "/home/user/a.txt": b"hello",
            "/home/user/b.txt": b"world!",
        }
    )
    async with run_browser(fs) as (pilot, browser, _):
        await pilot.press("down")  # move past ".." to first file
        await pilot.press("insert")  # select it
        await pilot.pause()

        strips = browser.render_lines(Region(0, browser.outer_size.height - 1, browser.outer_size.width, 1))
        bottom_text = strips[0].text
        assert "1 file" in bottom_text


class _SymlinkMockFilesystem(MockFilesystem):
    def stat(self, path: VPath) -> Stat:
        s = super().stat(path)
        if path.name == "link.txt":
            return Stat(
                size=s.size,
                modified=s.modified,
                is_hidden=s.is_hidden,
                is_symlink=True,
            )
        return s

    def readlink(self, path: VPath) -> str:
        if path.name == "link.txt":
            return "/some/target"
        raise OSError(f"Not a symbolic link: '{path}'")


@pytest.mark.asyncio
async def test_bottom_right_shows_symlink_placeholder_for_symlink() -> None:
    """Bottom-right shows '(symlink)' placeholder when cursor is on a symlink."""
    fs = _SymlinkMockFilesystem(files={"/home/user/link.txt": b""})

    async with run_browser(fs) as (pilot, browser, _):
        items = browser._shown_items
        symlink_row = next(i for i, p in enumerate(items) if p.name == "link.txt")
        browser.cursor_row = symlink_row
        await pilot.pause()

        strips = browser.render_lines(Region(0, browser.outer_size.height - 1, browser.outer_size.width, 1))
        bottom_text = strips[0].text
        assert "/some/target" in bottom_text
