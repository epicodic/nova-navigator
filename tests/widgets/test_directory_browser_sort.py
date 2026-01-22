"""Tests for DirectoryBrowser column sorting and sort direction."""

import pytest

from nova_navigator.widgets.directory_browser import DirectoryBrowser, UpPath
from tests.widgets._directory_browser_fixtures import run_browser, sized_files_fs


def _non_up_names(browser: DirectoryBrowser) -> list[str]:
    return [p.name for p in browser._shown_items if not isinstance(p, UpPath)]


@pytest.mark.asyncio
async def test_default_sort_dirs_before_files_alphabetical() -> None:
    async with run_browser(sized_files_fs()) as (_pilot, browser, _msgs):
        names = _non_up_names(browser)
        # Directories first (alpha_dir, beta_dir), then files (large.bin, medium.txt, small.py)
        assert names.index("alpha_dir") < names.index("beta_dir")
        assert names.index("beta_dir") < names.index("large.bin")
        assert names.index("large.bin") < names.index("medium.txt")
        assert names.index("medium.txt") < names.index("small.py")


@pytest.mark.asyncio
async def test_sort_ascending_false_reverses_order() -> None:
    async with run_browser(sized_files_fs()) as (pilot, browser, _msgs):
        browser.sort_ascending = False
        await pilot.pause()
        names = _non_up_names(browser)
        # With ascending=False: key=(-order, value) sorted reverse=True.
        # Dirs (group 2 → -2) still come before files (group 4 → -4) since -2 > -4.
        # Within each group the name is sorted reverse-alphabetically.
        assert names.index("beta_dir") < names.index("alpha_dir")
        assert names.index("alpha_dir") < names.index("small.py")
        assert names.index("small.py") < names.index("medium.txt")
        assert names.index("medium.txt") < names.index("large.bin")


@pytest.mark.asyncio
async def test_sort_by_size_column() -> None:
    async with run_browser(sized_files_fs()) as (pilot, browser, _msgs):
        browser.sort_column = 2  # Size column
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
        browser.sort_column = 3  # Modified column
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
async def test_changing_sort_column_resorts_items() -> None:
    async with run_browser(sized_files_fs()) as (pilot, browser, _msgs):
        assert browser.sort_column == 0
        # Directly set sort_column to simulate column-header click outcome
        # and verify the watcher fires (re-sort occurs).
        browser.sort_column = 2
        await pilot.pause()
        assert browser.sort_column == 2
        # After setting to size-column, file order should be smallest→largest
        file_names = [n for n in _non_up_names(browser) if "." in n]
        assert file_names == [
            "small.py",
            "medium.txt",
            "large.bin",
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
