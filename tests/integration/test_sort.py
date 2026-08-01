"""Integration tests for the sort keyboard shortcuts (Ctrl+T n/s/t/e).

The sort bindings live on ``MainScreen`` (the top-level widget) and delegate to
the active panel, so they can only be exercised through the full app.
"""

from __future__ import annotations

import os

import pytest

from nova_navigator.widgets.directory_browser import DirectoryBrowser, SortKey, UpPath
from tests.integration.conftest import AppCtx, set_panels


def _non_up_file_names(browser: DirectoryBrowser) -> list[str]:
    return [p.name for p in browser._shown_items if not isinstance(p, UpPath) and "." in p.name]


async def _populate(ctx: AppCtx) -> None:
    """Create files with distinct sizes, mtimes, and extensions in the source dir."""
    large = ctx.src_dir / "large.bin"
    medium = ctx.src_dir / "medium.txt"
    small = ctx.src_dir / "small.py"
    large.write_bytes(b"x" * 1000)
    medium.write_bytes(b"x" * 100)
    small.write_bytes(b"x" * 10)
    # large.bin oldest, small.py newest.
    os.utime(large, (1000, 1000))
    os.utime(medium, (2000, 2000))
    os.utime(small, (3000, 3000))
    await set_panels(ctx)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sort_by_size_shortcut(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    browser = app_ctx.screen._left_panel

    await app_ctx.pilot.press("ctrl+t", "s")
    await app_ctx.pilot.pause()

    assert browser.sort_column == SortKey.SIZE
    assert browser.sort_ascending is True
    assert _non_up_file_names(browser) == ["small.py", "medium.txt", "large.bin"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sort_by_modified_shortcut(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    browser = app_ctx.screen._left_panel

    await app_ctx.pilot.press("ctrl+t", "t")
    await app_ctx.pilot.pause()

    assert browser.sort_column == SortKey.MODIFIED
    assert _non_up_file_names(browser) == ["large.bin", "medium.txt", "small.py"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sort_by_name_shortcut(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    browser = app_ctx.screen._left_panel

    # Move away from name sort first, then back.
    await app_ctx.pilot.press("ctrl+t", "s")
    await app_ctx.pilot.pause()
    await app_ctx.pilot.press("ctrl+t", "n")
    await app_ctx.pilot.pause()

    assert browser.sort_column == SortKey.NAME
    assert _non_up_file_names(browser) == ["large.bin", "medium.txt", "small.py"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sort_by_extension_shortcut(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    browser = app_ctx.screen._left_panel

    await app_ctx.pilot.press("ctrl+t", "e")
    await app_ctx.pilot.pause()

    assert browser.sort_column == SortKey.EXTENSION
    # Extensions: bin < py < txt
    assert _non_up_file_names(browser) == ["large.bin", "small.py", "medium.txt"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_repeated_sort_shortcut_toggles_direction(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    browser = app_ctx.screen._left_panel

    await app_ctx.pilot.press("ctrl+t", "s")
    await app_ctx.pilot.pause()
    assert browser.sort_ascending is True

    await app_ctx.pilot.press("ctrl+t", "s")
    await app_ctx.pilot.pause()
    assert browser.sort_column == SortKey.SIZE
    assert browser.sort_ascending is False
    assert _non_up_file_names(browser) == ["large.bin", "medium.txt", "small.py"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sort_shortcut_targets_active_panel(app_ctx: AppCtx) -> None:
    """The sort shortcut sorts whichever panel currently has focus."""
    await _populate(app_ctx)
    left = app_ctx.screen._left_panel
    right = app_ctx.screen._right_panel

    await app_ctx.pilot.press("tab")  # focus right panel
    await app_ctx.pilot.pause()
    await app_ctx.pilot.press("ctrl+t", "s")
    await app_ctx.pilot.pause()

    assert right.sort_column == SortKey.SIZE
    assert left.sort_column == SortKey.NAME
