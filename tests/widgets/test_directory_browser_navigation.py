"""Tests for DirectoryBrowser keyboard navigation and path-selection messages."""

import pytest

from nova_navigator.widgets.directory_browser import DirectoryBrowser, UpPath
from tests.widgets._directory_browser_fixtures import (
    flat_dir_fs,
    nested_fs,
    run_browser,
)


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
