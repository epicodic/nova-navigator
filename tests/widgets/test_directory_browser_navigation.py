"""Tests for DirectoryBrowser keyboard navigation and path-selection messages."""

import threading
from collections.abc import AsyncIterator
from typing import override

import pytest

from nova_navigator.vfs.types import Stat
from nova_navigator.vfs.vpath import VPath
from nova_navigator.widgets.directory_browser import DirectoryBrowser, UpPath
from tests._utils.mock_filesystem import MockFilesystem
from tests.widgets._directory_browser_fixtures import (
    flat_dir_fs,
    nested_fs,
    run_browser,
)


class _SymlinkNavigationFilesystem(MockFilesystem):
    def stat(self, path: VPath) -> Stat:
        stat = super().stat(path)
        if path.name == "link.txt":
            return Stat(
                size=stat.size,
                modified=stat.modified,
                is_hidden=stat.is_hidden,
                is_directory=stat.is_directory,
                is_symlink=True,
            )
        return stat

    def readlink(self, path: VPath) -> str:
        if path.name == "link.txt":
            return "../wrong-target.txt"
        return super().readlink(path)

    def resolve_link(self, path: VPath) -> VPath:
        if path.name == "link.txt":
            return self.path("/resolved/target.txt")
        return super().resolve_link(path)


@pytest.mark.asyncio
async def test_initial_cursor_at_top() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        assert browser.cursor_row == 0


@pytest.mark.asyncio
async def test_cursor_down_moves_to_next_item() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        await pilot.press("down")
        await pilot.pause()
        assert browser.cursor_row == 1


@pytest.mark.asyncio
async def test_cursor_up_clamped_at_zero() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        await pilot.press("up")
        await pilot.pause()
        assert browser.cursor_row == 0


@pytest.mark.asyncio
async def test_home_goes_to_top() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("home")
        await pilot.pause()
        assert browser.cursor_row == 0


@pytest.mark.asyncio
async def test_end_goes_to_last() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        await pilot.press("end")
        await pilot.pause()
        assert browser.cursor_row == len(browser._shown_items) - 1


@pytest.mark.asyncio
async def test_enter_on_up_path_posts_path_selected_with_parent() -> None:
    # Navigate into nested_fs's child dir, then press Enter on `..`
    async with run_browser(nested_fs(), "/home/user/child") as (pilot, browser, msgs):
        # Row 0 is UpPath
        assert isinstance(browser._shown_items[0], UpPath)
        msgs.clear()
        await pilot.press("enter")
        await pilot.pause()
        path_selected = [m for m in msgs if isinstance(m, DirectoryBrowser.PathSelected)]
        assert len(path_selected) == 1
        assert str(path_selected[0].path.path) == "/home/user"


@pytest.mark.asyncio
async def test_enter_on_directory_posts_path_selected() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, msgs):
        # Move to `subdir` row
        target_row = next(i for i, p in enumerate(browser._shown_items) if p.name == "subdir")
        browser.cursor_row = target_row
        await pilot.pause()
        msgs.clear()
        await pilot.press("enter")
        await pilot.pause()
        path_selected = [m for m in msgs if isinstance(m, DirectoryBrowser.PathSelected)]
        assert len(path_selected) == 1
        assert path_selected[0].path.name == "subdir"


@pytest.mark.asyncio
async def test_enter_on_file_posts_path_selected() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, msgs):
        # Move to file_a.txt row
        target_row = next(i for i, p in enumerate(browser._shown_items) if p.name == "file_a.txt")
        browser.cursor_row = target_row
        await pilot.pause()
        msgs.clear()
        await pilot.press("enter")
        await pilot.pause()
        path_selected = [m for m in msgs if isinstance(m, DirectoryBrowser.PathSelected)]
        assert len(path_selected) == 1
        assert path_selected[0].path.name == "file_a.txt"


@pytest.mark.asyncio
async def test_double_click_directory_navigates_into_it() -> None:
    async with run_browser(nested_fs()) as (pilot, browser, _msgs):
        # Find the row index of `child`
        child_row = next(i for i, p in enumerate(browser._shown_items) if p.name == "child")
        # Move cursor to child row and use _action_select_cursor to simulate double-click
        browser.cursor_row = child_row
        await pilot.pause()
        browser._action_select_cursor()
        await pilot.pause()
        assert str(browser.path.path) == "/home/user/child"


@pytest.mark.asyncio
async def test_double_click_up_path_navigates_to_parent() -> None:
    async with run_browser(nested_fs(), "/home/user/child") as (pilot, browser, _msgs):
        # Row 0 is UpPath
        assert isinstance(browser._shown_items[0], UpPath)
        browser.cursor_row = 0
        await pilot.pause()
        browser._action_select_cursor()
        await pilot.pause()
        assert str(browser.path.path) == "/home/user"


@pytest.mark.asyncio
async def test_item_changed_message_posted_on_cursor_move() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, _browser, msgs):
        msgs.clear()
        await pilot.press("down")
        await pilot.pause()
        item_changed = [m for m in msgs if isinstance(m, DirectoryBrowser.ItemChanged)]
        assert len(item_changed) == 1


@pytest.mark.asyncio
async def test_follow_symlink_uses_filesystem_resolve_link() -> None:
    fs = _SymlinkNavigationFilesystem(
        files={
            "/home/user/link.txt": b"",
            "/resolved/target.txt": b"resolved",
        }
    )

    async with run_browser(fs) as (pilot, browser, msgs):
        browser.cursor_row = next(i for i, path in enumerate(browser._shown_items) if path.name == "link.txt")
        await pilot.pause()

        msgs.clear()
        browser.action_follow_symlink()
        await pilot.pause()

        path_selected = [m for m in msgs if isinstance(m, DirectoryBrowser.PathSelected)]
        assert len(path_selected) == 1
        assert path_selected[0].path == fs.path("/resolved/target.txt")


@pytest.mark.asyncio
async def test_setting_cursor_row_with_empty_shown_items_does_not_crash() -> None:
    """Setting cursor_row while _shown_items is empty must not raise IndexError.

    Regression: when set_path is called while a directory load is already in
    flight, _load_directory clears _shown_items to [] then the new set_path
    sets cursor_row = 0, triggering watch_cursor_row → path_item_under_cursor
    → _shown_items[0] → IndexError, killing the worker and freezing the panel.
    """
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        # Move the cursor to a non-zero row so the watcher will fire when we
        # reset to 0 (the watcher only runs when old_row != new_row).
        await pilot.press("down")
        await pilot.pause()
        assert browser.cursor_row > 0

        # Simulate what _load_directory does at the start of a new load.
        browser._shown_items = []
        browser.refresh()
        await pilot.pause()

        # This must not raise IndexError — the real crash site.
        browser.cursor_row = 0
        await pilot.pause()


class _PermissionDeniedFilesystem(MockFilesystem):
    """MockFilesystem that raises PermissionError when listing /home/user/restricted."""

    @override
    async def iterdir(self, path: VPath, *, cancel: threading.Event | None = None) -> AsyncIterator[VPath]:
        if path.path.name == "restricted":
            raise PermissionError(13, "Permission denied", str(path.path))
        async for vp in super().iterdir(path, cancel=cancel):
            yield vp


@pytest.mark.asyncio
async def test_permission_denied_leaves_path_unchanged() -> None:
    """Navigating into an inaccessible directory leaves the browser at its original path."""
    fs = _PermissionDeniedFilesystem(
        {
            "/home/user/file.txt": b"data",
            "/home/user/restricted": None,
        }
    )
    async with run_browser(fs) as (pilot, browser, _msgs):
        original_path = browser.path

        # Move cursor to 'restricted' and press Enter.
        browser.cursor_row = next(i for i, p in enumerate(browser._shown_items) if p.name == "restricted")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause(delay=0.2)

        assert browser.path == original_path
        assert not browser._loading
