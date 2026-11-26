"""Integration tests for Hide Identical Files in Compare Directories."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nova_navigator.vfs.vpath import VPath
from nova_navigator.widgets.directory_browser import DirectoryBrowser, UpPath
from nova_widgets.menu import Menu
from tests.integration.conftest import AppCtx, poll_until, set_panels

_T0 = 1_000_000_000
_T1 = 1_000_000_100


def _enable_compare(ctx: AppCtx) -> None:
    ctx.screen._act("view.compare_enable").set_checked(True)
    ctx.screen._action_toggle_compare_enable()


def _disable_compare(ctx: AppCtx) -> None:
    ctx.screen._act("view.compare_enable").set_checked(False)
    ctx.screen._action_toggle_compare_enable()


async def _wait_for_compare(ctx: AppCtx) -> None:
    await poll_until(
        ctx.pilot,
        lambda: not ctx.screen._left_panel._loading and not ctx.screen._right_panel._loading,
    )


def _set_hide_identical(ctx: AppCtx, checked: bool) -> None:
    ctx.screen._act("view.compare_hide_identical").set_checked(checked)
    ctx.screen._action_toggle_hide_identical()


def _find_menu(menus: list[Menu], menu_id: str) -> Menu | None:
    for menu in menus:
        if menu.id == menu_id:
            return menu
        found = _find_menu([a for a in menu._actions if isinstance(a, Menu)], menu_id)
        if found is not None:
            return found
    return None


def _shown(panel: DirectoryBrowser) -> set[str]:
    return {p.name for p in panel._shown_items if not isinstance(p, UpPath)}


def _write(path: Path, data: bytes, mtime: int) -> None:
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))


def _prepare_files(ctx: AppCtx) -> None:
    _write(ctx.src_dir / "same.txt", b"abcd", _T0)
    _write(ctx.dst_dir / "same.txt", b"abcd", _T0)
    _write(ctx.src_dir / "diff_size.txt", b"abcd", _T0)
    _write(ctx.dst_dir / "diff_size.txt", b"abcdef", _T0)
    _write(ctx.src_dir / "diff_mtime.txt", b"abcd", _T0)
    _write(ctx.dst_dir / "diff_mtime.txt", b"abcd", _T1)
    _write(ctx.src_dir / "left_only.txt", b"l", _T0)
    _write(ctx.dst_dir / "right_only.txt", b"r", _T0)
    (ctx.src_dir / "shared").mkdir()
    (ctx.dst_dir / "shared").mkdir()


async def _prepare(ctx: AppCtx) -> None:
    _prepare_files(ctx)
    await set_panels(ctx)


async def _settle(ctx: AppCtx) -> None:
    await _wait_for_compare(ctx)
    await ctx.pilot.pause()


async def _compare_with_hide(ctx: AppCtx, mode: str | None) -> None:
    """Enable compare, pick *mode* ("size", "mtime" or None), check Hide Identical."""
    _enable_compare(ctx)
    await _settle(ctx)
    if mode == "size":
        ctx.screen._action_compare_by_size()
    elif mode == "mtime":
        ctx.screen._action_compare_by_mtime()
    _set_hide_identical(ctx, True)
    await _settle(ctx)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_hide_identical_action_is_registered_checkable(app_ctx: AppCtx) -> None:
    action = app_ctx.screen._act("view.compare_hide_identical")

    assert action.checkable
    assert action.initial_shortcut is None
    menu = _find_menu(list(app_ctx.screen._menu_bar._menus), "compare_directories")
    assert menu is not None
    assert action in menu._actions


@pytest.mark.asyncio
@pytest.mark.integration
async def test_hide_identical_by_size_hides_equal_files_in_both_panels(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, "size")

    for panel in (app_ctx.screen._left_panel, app_ctx.screen._right_panel):
        assert "same.txt" not in _shown(panel)
        assert "diff_mtime.txt" not in _shown(panel)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_hide_identical_by_size_keeps_different_size(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, "size")

    assert "diff_size.txt" in _shown(app_ctx.screen._left_panel)
    assert "diff_size.txt" in _shown(app_ctx.screen._right_panel)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_hide_identical_by_mtime_hides_equal_mtime(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, "mtime")

    for panel in (app_ctx.screen._left_panel, app_ctx.screen._right_panel):
        assert "same.txt" not in _shown(panel)
        assert "diff_size.txt" not in _shown(panel)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_hide_identical_by_mtime_keeps_different_mtime(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, "mtime")

    assert "diff_mtime.txt" in _shown(app_ctx.screen._left_panel)
    assert "diff_mtime.txt" in _shown(app_ctx.screen._right_panel)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_one_sided_files_and_folders_stay_visible(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, "size")

    for _ in range(2):
        left = _shown(app_ctx.screen._left_panel)
        right = _shown(app_ctx.screen._right_panel)
        assert {"left_only.txt", "shared"} <= left
        assert {"right_only.txt", "shared"} <= right
        app_ctx.screen._action_compare_by_mtime()
        await _settle(app_ctx)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_uncheck_shows_files_again(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, "size")
    assert "same.txt" not in _shown(app_ctx.screen._left_panel)

    _set_hide_identical(app_ctx, False)
    await _settle(app_ctx)

    assert "same.txt" in _shown(app_ctx.screen._left_panel)
    assert "same.txt" in _shown(app_ctx.screen._right_panel)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_disable_compare_shows_files_again(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, "size")
    assert "same.txt" not in _shown(app_ctx.screen._left_panel)

    _disable_compare(app_ctx)
    await _settle(app_ctx)

    assert "same.txt" in _shown(app_ctx.screen._left_panel)
    assert "same.txt" in _shown(app_ctx.screen._right_panel)
    assert app_ctx.screen._act("view.compare_hide_identical").checked


@pytest.mark.asyncio
@pytest.mark.integration
async def test_reenable_compare_needs_mode_again(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, "size")
    _disable_compare(app_ctx)
    await _settle(app_ctx)

    _enable_compare(app_ctx)
    await _settle(app_ctx)
    assert "same.txt" in _shown(app_ctx.screen._left_panel)

    app_ctx.screen._action_compare_by_size()
    await _settle(app_ctx)
    assert "same.txt" not in _shown(app_ctx.screen._left_panel)
    assert "same.txt" not in _shown(app_ctx.screen._right_panel)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_mode_change_reevaluates(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, "size")
    assert "diff_mtime.txt" not in _shown(app_ctx.screen._left_panel)

    app_ctx.screen._action_compare_by_mtime()
    await _settle(app_ctx)
    assert "diff_mtime.txt" in _shown(app_ctx.screen._left_panel)
    assert "diff_mtime.txt" in _shown(app_ctx.screen._right_panel)

    app_ctx.screen._action_compare_by_size()
    await _settle(app_ctx)
    assert "diff_mtime.txt" not in _shown(app_ctx.screen._left_panel)
    assert "diff_mtime.txt" not in _shown(app_ctx.screen._right_panel)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_directory_change_reevaluates(app_ctx: AppCtx, tmp_path: Path) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, "size")
    assert "same.txt" not in _shown(app_ctx.screen._left_panel)

    src2 = tmp_path / "src2"
    dst2 = tmp_path / "dst2"
    src2.mkdir()
    dst2.mkdir()
    _write(src2 / "equal2.txt", b"xy", _T0)
    _write(dst2 / "equal2.txt", b"xy", _T0)
    _write(src2 / "other2.txt", b"xy", _T0)
    _write(dst2 / "other2.txt", b"xyz", _T0)

    app_ctx.screen._left_panel.set_path(VPath(src2, app_ctx.fs))
    app_ctx.screen._right_panel.set_path(VPath(dst2, app_ctx.fs))
    await poll_until(app_ctx.pilot, lambda: "other2.txt" in _shown(app_ctx.screen._right_panel) and "equal2.txt" not in _shown(app_ctx.screen._right_panel))
    await _settle(app_ctx)

    for panel in (app_ctx.screen._left_panel, app_ctx.screen._right_panel):
        assert "equal2.txt" not in _shown(panel)
        assert "other2.txt" in _shown(panel)
        assert "same.txt" not in _shown(panel)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_file_change_on_disk_reevaluates(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, "size")
    assert "same.txt" not in _shown(app_ctx.screen._left_panel)

    _write(app_ctx.src_dir / "same.txt", b"abcdefgh", _T0)
    app_ctx.screen.reload_panels()
    await poll_until(app_ctx.pilot, lambda: "same.txt" in _shown(app_ctx.screen._left_panel))
    await _settle(app_ctx)

    assert "same.txt" in _shown(app_ctx.screen._left_panel)
    assert "same.txt" in _shown(app_ctx.screen._right_panel)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_mode_none_hides_nothing(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)
    await _compare_with_hide(app_ctx, None)

    assert {"same.txt", "diff_size.txt", "diff_mtime.txt"} <= _shown(app_ctx.screen._left_panel)
    assert {"same.txt", "diff_size.txt", "diff_mtime.txt"} <= _shown(app_ctx.screen._right_panel)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_toggle_while_compare_disabled_hides_nothing(app_ctx: AppCtx) -> None:
    await _prepare(app_ctx)

    _set_hide_identical(app_ctx, True)
    await _settle(app_ctx)
    assert "same.txt" in _shown(app_ctx.screen._left_panel)
    assert "same.txt" in _shown(app_ctx.screen._right_panel)

    _enable_compare(app_ctx)
    await _settle(app_ctx)
    app_ctx.screen._action_compare_by_size()
    await _settle(app_ctx)
    assert "same.txt" not in _shown(app_ctx.screen._left_panel)
    assert "same.txt" not in _shown(app_ctx.screen._right_panel)
