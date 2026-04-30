"""Tests for DirectoryBrowser.reload() and set_path() cache eviction."""

from __future__ import annotations

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
