"""Tests for DirectoryBrowser item selection mechanics."""

import pytest

from nova_navigator.widgets.directory_browser import UpPath
from tests.widgets._directory_browser_fixtures import flat_dir_fs, run_browser


@pytest.mark.asyncio
async def test_insert_selects_item_and_moves_cursor_down() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        # Move to row 1 (first real file/dir after `..`)
        await pilot.press("down")
        await pilot.pause()
        item = browser._shown_items[1]
        await pilot.press("insert")
        await pilot.pause()
        assert item in browser._selected_items
        assert browser.cursor_row == 2


@pytest.mark.asyncio
async def test_insert_on_up_path_only_moves_cursor() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        assert browser.cursor_row == 0
        assert isinstance(browser._shown_items[0], UpPath)
        await pilot.press("insert")
        await pilot.pause()
        assert len(browser._selected_items) == 0
        assert browser.cursor_row == 1


@pytest.mark.asyncio
async def test_insert_twice_deselects_item() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        await pilot.press("down")
        await pilot.pause()
        item = browser._shown_items[1]
        await pilot.press("insert")
        await pilot.pause()
        assert item in browser._selected_items
        # Move back to row 1 to insert again
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("insert")
        await pilot.pause()
        assert item not in browser._selected_items


@pytest.mark.asyncio
async def test_ctrl_a_selects_all_non_up_items() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        await pilot.press("ctrl+a")
        await pilot.pause()
        expected = {item for item in browser._shown_items if not isinstance(item, UpPath)}
        assert browser._selected_items == expected


@pytest.mark.asyncio
async def test_selected_path_items_returns_cursor_item_when_nothing_selected() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        await pilot.press("down")
        await pilot.pause()
        cursor_item = browser._shown_items[browser.cursor_row]
        assert not isinstance(cursor_item, UpPath)
        assert browser.selected_path_items == [cursor_item]


@pytest.mark.asyncio
async def test_selected_path_items_returns_selection_when_items_selected() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        await pilot.press("ctrl+a")
        await pilot.pause()
        expected = {item for item in browser._shown_items if not isinstance(item, UpPath)}
        assert set(browser.selected_path_items) == expected
