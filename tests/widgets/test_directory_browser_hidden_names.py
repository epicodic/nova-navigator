"""Tests for DirectoryBrowser.set_hidden_names()."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest
from textual.pilot import Pilot

from nova_navigator.vfs.vpath import VPath
from nova_navigator.widgets.directory_browser import DirectoryBrowser, UpPath
from tests._utils.mock_filesystem import MockFilesystem
from tests.widgets._directory_browser_fixtures import flat_dir_fs, run_browser


def _names(browser: DirectoryBrowser) -> list[str]:
    return [p.name for p in browser._shown_items]


async def _wait_loaded(pilot: Pilot, browser: DirectoryBrowser, path: VPath) -> None:
    """Wait until the browser shows *path* and has finished loading it."""
    for _ in range(100):
        await pilot.pause(delay=0.02)
        if browser._path == path and not browser._loading and browser._pending is None:
            return
    pytest.fail("directory did not finish loading")


@pytest.mark.asyncio
async def test_set_hidden_names_removes_items_from_shown() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        browser.set_hidden_names(frozenset({"file_a.txt"}))

        names = _names(browser)
        assert "file_a.txt" not in names
        assert "file_b.py" in names
        assert "subdir" in names


@pytest.mark.asyncio
async def test_hidden_names_keep_items_in_all_items() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        browser.set_hidden_names(frozenset({"file_a.txt"}))

        assert "file_a.txt" in {p.name for p in browser.items}


@pytest.mark.asyncio
async def test_hidden_names_never_hide_up_path() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        browser.set_hidden_names(frozenset({".."}))

        assert isinstance(browser._shown_items[0], UpPath)


@pytest.mark.asyncio
async def test_set_hidden_names_none_restores() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        before = _names(browser)
        browser.set_hidden_names(frozenset({"file_a.txt"}))
        browser.set_hidden_names(None)

        assert _names(browser) == before


@pytest.mark.asyncio
async def test_set_hidden_names_empty_set_restores() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        before = _names(browser)
        browser.set_hidden_names(frozenset({"file_a.txt"}))
        browser.set_hidden_names(frozenset())

        assert _names(browser) == before


@pytest.mark.asyncio
async def test_hidden_names_apply_to_fast_path() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        browser.show_hidden_files = True
        browser.set_hidden_names(frozenset({"file_b.py"}))

        assert "file_b.py" not in _names(browser)
        assert "file_a.txt" in _names(browser)


@pytest.mark.asyncio
async def test_hidden_names_apply_to_incremental_append() -> None:
    fs = flat_dir_fs()
    async with run_browser(fs) as (_pilot, browser, _msgs):
        browser.set_hidden_names(frozenset({"new_hidden.txt"}))
        before = len(browser._shown_items)
        new_hidden = VPath(PurePosixPath("/home/user/new_hidden.txt"), fs)
        new_shown = VPath(PurePosixPath("/home/user/new_shown.txt"), fs)
        new_hidden._stat = fs.stat(VPath(PurePosixPath("/home/user/file_a.txt"), fs))
        new_shown._stat = new_hidden._stat

        browser._append_items([new_hidden, new_shown])

        assert len(browser._shown_items) == before + 1
        assert browser._shown_items[-1] is new_shown


@pytest.mark.asyncio
async def test_hidden_names_cleared_on_navigation_to_other_directory() -> None:
    fs = MockFilesystem({"/home/user/child/data.bin": b"binary", "/home/user/file.txt": b"x"})
    async with run_browser(fs) as (pilot, browser, _msgs):
        browser.set_hidden_names(frozenset({"x"}))
        child = VPath(PurePosixPath("/home/user/child"), fs)

        browser.set_path(child)
        await _wait_loaded(pilot, browser, child)

        assert browser._hidden_names == frozenset()


@pytest.mark.asyncio
async def test_hidden_names_kept_on_reload_of_same_directory() -> None:
    async with run_browser(flat_dir_fs()) as (pilot, browser, _msgs):
        browser.set_hidden_names(frozenset({"file_a.txt"}))

        browser.reload()
        await pilot.pause(delay=0.2)
        await _wait_loaded(pilot, browser, browser._path)

        assert "file_a.txt" not in _names(browser)


@pytest.mark.asyncio
async def test_hidden_names_drop_selection_of_hidden_items() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        item = next(p for p in browser._shown_items if p.name == "file_a.txt")
        browser._selected_items.add(item)

        browser.set_hidden_names(frozenset({"file_a.txt"}))

        assert item not in browser._selected_items
