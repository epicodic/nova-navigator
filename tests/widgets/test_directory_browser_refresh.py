"""Tests for DirectoryBrowser.reload() and set_path() cache eviction."""

from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import patch

import pytest

from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem
from tests.widgets._directory_browser_fixtures import run_browser


@pytest.mark.asyncio
async def test_reload_calls_filesystem_refresh() -> None:
    """reload() must call filesystem.refresh() with no args."""
    fs = MockFilesystem({"/home/user/file.txt": b"data"})
    async with run_browser(fs) as (_pilot, browser, _msgs):
        with patch.object(fs, "refresh") as spy:
            browser.reload()
            spy.assert_called_once_with()


@pytest.mark.asyncio
async def test_set_path_calls_filesystem_refresh_with_path() -> None:
    """set_path(new_path) must call filesystem.refresh(new_path) before loading."""
    fs = MockFilesystem(
        {
            "/home/user/file.txt": b"data",
            "/home/user/subdir": None,
            "/home/user/subdir/inner.txt": b"inner",
        }
    )
    async with run_browser(fs) as (_pilot, browser, _msgs):
        with patch.object(fs, "refresh") as spy:
            new_path = VPath("/home/user/subdir", fs)
            browser.set_path(new_path)
            call_args = [call.args[0] for call in spy.call_args_list if call.args]
            assert any(str(p) == "/home/user/subdir" for p in call_args)


@pytest.mark.asyncio
async def test_cursor_moves_to_next_item_after_item_under_cursor_deleted() -> None:
    """When the file under the cursor is deleted, reload() must place the cursor
    on the item that was directly after it (same row index) rather than row 0.

    Layout before deletion (ascending name sort):
      row 0: ..          (UpPath)
      row 1: file_a.txt
      row 2: file_b.txt  ← cursor here
      row 3: file_c.txt

    After file_b.txt is deleted and reload completes:
      row 0: ..
      row 1: file_a.txt
      row 2: file_c.txt  ← cursor should land here, not row 0
    """
    fs = MockFilesystem(
        {
            "/home/user/file_a.txt": b"a",
            "/home/user/file_b.txt": b"b",
            "/home/user/file_c.txt": b"c",
        }
    )
    async with run_browser(fs) as (pilot, browser, _msgs):
        browser.cursor_row = 2  # file_b.txt
        del fs._nodes[PurePosixPath("/home/user/file_b.txt")]
        browser.reload()
        await pilot.pause()
        assert browser.cursor_row == 2


@pytest.mark.asyncio
async def test_cursor_clamped_to_last_item_when_last_item_deleted() -> None:
    """When the last item in the list is deleted, reload() must clamp the cursor
    to the new last item rather than leaving it at row 0.

    Layout before deletion:
      row 0: ..          (UpPath)
      row 1: file_a.txt
      row 2: file_b.txt  ← cursor here (last item)

    After file_b.txt is deleted and reload completes:
      row 0: ..
      row 1: file_a.txt  ← cursor should land here (clamped to last)
    """
    fs = MockFilesystem(
        {
            "/home/user/file_a.txt": b"a",
            "/home/user/file_b.txt": b"b",
        }
    )
    async with run_browser(fs) as (pilot, browser, _msgs):
        browser.cursor_row = 2  # file_b.txt (last)
        del fs._nodes[PurePosixPath("/home/user/file_b.txt")]
        browser.reload()
        await pilot.pause()
        assert browser.cursor_row == 1
