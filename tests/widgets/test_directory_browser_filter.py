"""Tests for DirectoryBrowser filtering and hidden-file visibility."""

import pytest
from textual.widgets import Input

from nova_navigator.widgets.directory_browser import DirectoryBrowser, UpPath
from tests.widgets._directory_browser_fixtures import (
    flat_dir_fs,
    hidden_files_fs,
    run_browser,
)


def _set_filter(browser: DirectoryBrowser, value: str) -> None:
    """Inject a filter value directly, bypassing the overlay focus flow.

    NOTE: The FilterWidget overlay has a known visibility bug in the application.
    If the bug manifests in tests as well, do NOT fix it — drive filtering via
    this helper instead, which calls the internal handler directly.
    """
    browser._filter_widget.input.value = value
    browser.on_filter_widget_input_changed(Input.Changed(browser._filter_widget.input, value))


@pytest.mark.asyncio
async def test_filter_hides_non_matching_items() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        _set_filter(browser, "file_a")
        await _pilot.pause()
        names = [p.name for p in browser._shown_items if not isinstance(p, UpPath)]
        assert names == ["file_a.txt"]


@pytest.mark.asyncio
async def test_filter_is_case_insensitive() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        _set_filter(browser, "FILE_A")
        await _pilot.pause()
        names = [p.name for p in browser._shown_items if not isinstance(p, UpPath)]
        assert names == ["file_a.txt"]


@pytest.mark.asyncio
async def test_filter_cleared_restores_all_items() -> None:
    async with run_browser(flat_dir_fs()) as (_pilot, browser, _msgs):
        _set_filter(browser, "file_a")
        await _pilot.pause()
        _set_filter(browser, "")
        await _pilot.pause()
        non_up = [p for p in browser._shown_items if not isinstance(p, UpPath)]
        assert len(non_up) == 3  # file_a.txt, file_b.py, subdir


@pytest.mark.asyncio
async def test_hidden_files_hidden_by_default() -> None:
    async with run_browser(hidden_files_fs()) as (_pilot, browser, _msgs):
        assert browser.show_hidden_files is False
        names = {p.name for p in browser._shown_items if not isinstance(p, UpPath)}
        assert ".hidden" not in names
        assert ".hidden_dir" not in names
        assert "visible.txt" in names


@pytest.mark.asyncio
async def test_show_hidden_files_includes_dotfiles() -> None:
    async with run_browser(hidden_files_fs()) as (pilot, browser, _msgs):
        browser.show_hidden_files = True
        await pilot.pause()
        names = {p.name for p in browser._shown_items if not isinstance(p, UpPath)}
        assert ".hidden" in names
        assert ".hidden_dir" in names
        assert "visible.txt" in names
