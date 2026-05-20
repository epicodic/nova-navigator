"""Integration tests for the Compare Directories feature."""

from __future__ import annotations

import pytest

from nova_navigator.filemanager.compare import (
    DIFFERENT_COLOR,
    LEFT_ONLY_COLOR,
    RIGHT_ONLY_COLOR,
)
from tests.integration.conftest import AppCtx, poll_until, set_panels


def _enable_compare(ctx: AppCtx) -> None:
    """Enable compare mode (pre-toggle checked state, then call handler)."""
    ctx.screen._act("view.compare_directories.compare_enable").set_checked(True)
    ctx.screen._action_toggle_compare_enable()


def _disable_compare(ctx: AppCtx) -> None:
    """Disable compare mode."""
    ctx.screen._act("view.compare_directories.compare_enable").set_checked(False)
    ctx.screen._action_toggle_compare_enable()


def _enable_compare_by_size(ctx: AppCtx) -> None:
    """Switch compare mode to BY_SIZE."""
    ctx.screen._act("view.compare_directories.compare_by_size").set_checked(True)
    ctx.screen._action_compare_by_size()


async def _wait_for_compare(ctx: AppCtx) -> None:
    """Wait until both panels have finished loading and the comparison has run."""
    await poll_until(
        ctx.pilot,
        lambda: not ctx.screen._left_panel._loading and not ctx.screen._right_panel._loading,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_compare_colors_left_only_file(app_ctx: AppCtx) -> None:
    """A file that exists only in the left panel is colored LEFT_ONLY_COLOR."""
    (app_ctx.src_dir / "left_only.txt").write_bytes(b"left")

    await set_panels(app_ctx)
    _enable_compare(app_ctx)
    await _wait_for_compare(app_ctx)

    assert app_ctx.screen._left_panel._item_colors.get("left_only.txt") == LEFT_ONLY_COLOR
    assert "left_only.txt" not in app_ctx.screen._right_panel._item_colors


@pytest.mark.asyncio
@pytest.mark.integration
async def test_compare_colors_right_only_file(app_ctx: AppCtx) -> None:
    """A file that exists only in the right panel is colored RIGHT_ONLY_COLOR."""
    (app_ctx.dst_dir / "right_only.txt").write_bytes(b"right")

    await set_panels(app_ctx)
    _enable_compare(app_ctx)
    await _wait_for_compare(app_ctx)

    assert app_ctx.screen._right_panel._item_colors.get("right_only.txt") == RIGHT_ONLY_COLOR
    assert "right_only.txt" not in app_ctx.screen._left_panel._item_colors


@pytest.mark.asyncio
@pytest.mark.integration
async def test_compare_colors_differing_file_by_size(app_ctx: AppCtx) -> None:
    """Files with same name but different size are colored DIFFERENT_COLOR when mode=BY_SIZE."""
    (app_ctx.src_dir / "shared.txt").write_bytes(b"small")
    (app_ctx.dst_dir / "shared.txt").write_bytes(b"much larger content here")

    await set_panels(app_ctx)
    _enable_compare(app_ctx)
    _enable_compare_by_size(app_ctx)
    await _wait_for_compare(app_ctx)

    assert app_ctx.screen._left_panel._item_colors.get("shared.txt") == DIFFERENT_COLOR
    assert app_ctx.screen._right_panel._item_colors.get("shared.txt") == DIFFERENT_COLOR


@pytest.mark.asyncio
@pytest.mark.integration
async def test_compare_identical_file_gets_no_color_by_size(app_ctx: AppCtx) -> None:
    """Files with same name and same size are not colored when mode=BY_SIZE."""
    content = b"identical content"
    (app_ctx.src_dir / "same.txt").write_bytes(content)
    (app_ctx.dst_dir / "same.txt").write_bytes(content)

    await set_panels(app_ctx)
    _enable_compare(app_ctx)
    _enable_compare_by_size(app_ctx)
    await _wait_for_compare(app_ctx)

    assert "same.txt" not in app_ctx.screen._left_panel._item_colors
    assert "same.txt" not in app_ctx.screen._right_panel._item_colors


@pytest.mark.asyncio
@pytest.mark.integration
async def test_disable_compare_clears_item_colors(app_ctx: AppCtx) -> None:
    """Disabling compare clears item colors on both panels."""
    (app_ctx.src_dir / "only_left.txt").write_bytes(b"x")

    await set_panels(app_ctx)
    _enable_compare(app_ctx)
    await _wait_for_compare(app_ctx)

    assert app_ctx.screen._left_panel._item_colors.get("only_left.txt") == LEFT_ONLY_COLOR

    _disable_compare(app_ctx)
    await app_ctx.pilot.pause()

    assert app_ctx.screen._left_panel._item_colors == {}
    assert app_ctx.screen._right_panel._item_colors == {}
