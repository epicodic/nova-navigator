"""Tests for DirectoryBrowser.apply_pattern_selection."""

from unittest.mock import patch

import pytest
from textual.widgets import Input

from nova_navigator.file_filter import FilenamePatternFilter
from nova_navigator.vfs.vpath import VPath
from nova_navigator.widgets.directory_browser import DirectoryBrowser, UpPath
from tests._utils.mock_filesystem import MockFilesystem
from tests.widgets._directory_browser_fixtures import run_browser


def _pattern_fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/a.py": b"a",
            "/home/user/b.txt": b"b",
            "/home/user/c.md": b"c",
            "/home/user/d.PY": b"d",
            "/home/user/.hid.py": b"h",
            "/home/user/build.py": None,
            "/home/user/docs": None,
        }
    )


def _names(browser: DirectoryBrowser) -> set[str]:
    return {p.name for p in browser._selected_items}


def _filt(pattern: str) -> FilenamePatternFilter:
    return FilenamePatternFilter.from_pattern_string(pattern)


def _preselect(browser: DirectoryBrowser, *names: str) -> None:
    browser._selected_items |= {i for i in browser._shown_items if i.name in names}


def _set_filter(browser: DirectoryBrowser, value: str) -> None:
    browser._filter_widget.input.value = value
    browser.on_filter_widget_input_changed(Input.Changed(browser._filter_widget.input, value))


def _vpaths(browser: DirectoryBrowser) -> list[VPath]:
    return [i for i in browser._shown_items if not isinstance(i, UpPath)]


@pytest.mark.asyncio
async def test_select_matches_any_pattern() -> None:
    async with run_browser(_pattern_fs()) as (_pilot, browser, _msgs):
        browser.apply_pattern_selection(_filt("*.py;*.txt"), include_folders=False, select=True)
        assert _names(browser) == {"a.py", "b.txt"}


@pytest.mark.asyncio
async def test_select_keeps_existing_selection() -> None:
    async with run_browser(_pattern_fs()) as (_pilot, browser, _msgs):
        _preselect(browser, "c.md")
        browser.apply_pattern_selection(_filt("*.py"), include_folders=False, select=True)
        assert _names(browser) == {"a.py", "c.md"}


@pytest.mark.asyncio
async def test_unselect_removes_only_matching_selected() -> None:
    async with run_browser(_pattern_fs()) as (_pilot, browser, _msgs):
        browser.action_select_all()
        browser.apply_pattern_selection(_filt("*.py"), include_folders=False, select=False)
        assert _names(browser) == {"b.txt", "c.md", "d.PY", "build.py", "docs"}


@pytest.mark.asyncio
async def test_folders_skipped_without_include_folders() -> None:
    async with run_browser(_pattern_fs()) as (_pilot, browser, _msgs):
        browser.apply_pattern_selection(_filt("*.py"), include_folders=False, select=True)
        assert "build.py" not in _names(browser)


@pytest.mark.asyncio
async def test_folders_selected_with_include_folders() -> None:
    async with run_browser(_pattern_fs()) as (_pilot, browser, _msgs):
        browser.apply_pattern_selection(_filt("*.py"), include_folders=True, select=True)
        names = _names(browser)
        assert "build.py" in names
        assert "docs" not in names


@pytest.mark.asyncio
async def test_unselect_folders_only_with_include_folders() -> None:
    async with run_browser(_pattern_fs()) as (_pilot, browser, _msgs):
        browser.action_select_all()
        browser.apply_pattern_selection(_filt("*.py"), include_folders=False, select=False)
        assert "build.py" in _names(browser)
        browser.apply_pattern_selection(_filt("*.py"), include_folders=True, select=False)
        assert "build.py" not in _names(browser)


@pytest.mark.asyncio
async def test_up_path_never_selected() -> None:
    async with run_browser(_pattern_fs()) as (_pilot, browser, _msgs):
        browser.apply_pattern_selection(_filt("*"), include_folders=True, select=True)
        assert not any(isinstance(i, UpPath) for i in browser._selected_items)


@pytest.mark.asyncio
async def test_hidden_entries_not_shown_are_not_selected() -> None:
    async with run_browser(_pattern_fs()) as (_pilot, browser, _msgs):
        browser.apply_pattern_selection(_filt("*.py"), include_folders=False, select=True)
        assert ".hid.py" not in _names(browser)


@pytest.mark.asyncio
async def test_hidden_entries_selected_when_shown() -> None:
    async with run_browser(_pattern_fs()) as (pilot, browser, _msgs):
        browser.show_hidden_files = True
        await pilot.pause()
        browser.apply_pattern_selection(_filt("*.py"), include_folders=False, select=True)
        assert ".hid.py" in _names(browser)


@pytest.mark.asyncio
async def test_text_filtered_entries_not_selected() -> None:
    async with run_browser(_pattern_fs()) as (pilot, browser, _msgs):
        _set_filter(browser, "a")
        await pilot.pause()
        browser.apply_pattern_selection(_filt("*"), include_folders=False, select=True)
        assert _names(browser) == {"a.py"}


@pytest.mark.asyncio
async def test_no_match_changes_nothing() -> None:
    async with run_browser(_pattern_fs()) as (_pilot, browser, _msgs):
        browser.apply_pattern_selection(_filt("*.zzz"), include_folders=False, select=True)
        assert _names(browser) == set()
        _preselect(browser, "c.md")
        browser.apply_pattern_selection(_filt("*.zzz"), include_folders=False, select=True)
        assert _names(browser) == {"c.md"}


@pytest.mark.asyncio
async def test_empty_pattern_selects_all_files() -> None:
    async with run_browser(_pattern_fs()) as (_pilot, browser, _msgs):
        browser.apply_pattern_selection(_filt(""), include_folders=False, select=True)
        expected = {i.name for i in _vpaths(browser) if not i.stat.is_directory}
        assert expected
        assert _names(browser) == expected


@pytest.mark.asyncio
async def test_apply_refreshes_border() -> None:
    async with run_browser(_pattern_fs()) as (_pilot, browser, _msgs):
        with patch.object(browser, "refresh_border") as refresh_border:
            browser.apply_pattern_selection(_filt("*.py"), include_folders=False, select=True)
        refresh_border.assert_called_once()
