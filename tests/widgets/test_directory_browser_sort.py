"""Tests for DirectoryBrowser column sorting and sort direction."""

import pytest

from nova_navigator.widgets.directory_browser import DirectoryBrowser, SortKey, UpPath
from tests.widgets._directory_browser_fixtures import run_browser, sized_files_fs


def _non_up_names(browser: DirectoryBrowser) -> list[str]:
    return [p.name for p in browser._shown_items if not isinstance(p, UpPath)]


@pytest.mark.asyncio
async def test_sort_by_size_column() -> None:
    async with run_browser(sized_files_fs()) as (pilot, browser, _msgs):
        browser.sort_column = SortKey.SIZE
        browser.sort_ascending = True
        await pilot.pause()
        names = _non_up_names(browser)
        # Dirs first (size 0), then files smallest→largest
        file_names = [n for n in names if "." in n]
        assert file_names == [
            "small.py",
            "medium.txt",
            "large.bin",
        ]


@pytest.mark.asyncio
async def test_sort_by_modified_column() -> None:
    async with run_browser(sized_files_fs()) as (pilot, browser, _msgs):
        browser.sort_column = SortKey.MODIFIED
        browser.sort_ascending = True
        await pilot.pause()
        names = _non_up_names(browser)
        # large.bin has oldest mtime, small.py newest (set in sized_files_fs)
        file_names = [n for n in names if "." in n]
        assert file_names == [
            "large.bin",
            "medium.txt",
            "small.py",
        ]


@pytest.mark.asyncio
async def test_toggling_sort_ascending_reverses_order() -> None:
    async with run_browser(sized_files_fs()) as (pilot, browser, _msgs):
        assert browser.sort_ascending is True
        # Default ascending order: dirs alpha, then files alpha
        assert _non_up_names(browser) == [
            "alpha_dir",
            "beta_dir",
            "large.bin",
            "medium.txt",
            "small.py",
        ]

        browser.sort_ascending = False
        await pilot.pause()
        assert browser.sort_ascending is False
        # Descending order: dirs reverse-alpha, then files reverse-alpha
        assert _non_up_names(browser) == [
            "beta_dir",
            "alpha_dir",
            "small.py",
            "medium.txt",
            "large.bin",
        ]

        browser.sort_ascending = True
        await pilot.pause()
        assert browser.sort_ascending is True
        # Back to ascending order
        assert _non_up_names(browser) == [
            "alpha_dir",
            "beta_dir",
            "large.bin",
            "medium.txt",
            "small.py",
        ]


@pytest.mark.asyncio
async def test_repeated_sort_toggles_direction() -> None:
    async with run_browser(sized_files_fs()) as (pilot, browser, _msgs):
        browser.action_sort_by_size()
        await pilot.pause()
        assert browser.sort_column == SortKey.SIZE
        assert browser.sort_ascending is True
        browser.action_sort_by_size()
        await pilot.pause()
        assert browser.sort_column == SortKey.SIZE
        assert browser.sort_ascending is False
        file_names = [n for n in _non_up_names(browser) if "." in n]
        assert file_names == ["large.bin", "medium.txt", "small.py"]
