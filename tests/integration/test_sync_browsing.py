"""Integration tests for synchronized browsing.

When sync browsing is enabled both panels navigate in lock-step:
- The relative delta from the *source* panel's base is applied to the
  *other* panel's base.
- A filesystem change auto-disables sync and shows a warning.
- If the mirrored target does not exist the source panel (and terminal) are
  rolled back to the previous position.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import MagicMock, patch

import pytest

from nova_navigator.terminal import Terminal
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem
from tests.integration.conftest import AppCtx, poll_until, set_panels

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def enable_sync(ctx: AppCtx) -> None:
    """Enable synchronized browsing on the screen under test."""
    # Simulate what the menu does: pre-toggle checked before calling the action.
    ctx.screen._act("view.sync_browsing").set_checked(True)
    ctx.screen._action_toggle_sync_browsing()


def disable_sync(ctx: AppCtx) -> None:
    """Disable synchronized browsing on the screen under test."""
    ctx.screen._act("view.sync_browsing").set_checked(False)
    ctx.screen._action_toggle_sync_browsing()


# ---------------------------------------------------------------------------
# Basic mirroring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_browsing_mirrors_navigation_to_other_panel(app_ctx: AppCtx) -> None:
    """Navigating the active panel mirrors the relative delta to the other panel.

    Layout:
        left_base  = src_dir/
        right_base = dst_dir/
        both have a 'sub/' subdirectory

    When the left panel navigates into src_dir/sub/, the right panel should
    navigate to dst_dir/sub/.
    """
    (app_ctx.src_dir / "sub").mkdir()
    (app_ctx.dst_dir / "sub").mkdir()
    await set_panels(app_ctx)
    enable_sync(app_ctx)

    app_ctx.screen._left_panel.set_path(VPath(app_ctx.src_dir / "sub", app_ctx.fs))
    await app_ctx.pilot.pause()

    assert app_ctx.screen._right_panel.path.path == PurePosixPath(app_ctx.dst_dir / "sub")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_browsing_mirrors_navigation_above_base(app_ctx: AppCtx) -> None:
    """Navigating above the base directory applies the correct delta.

    Layout:
        left_base  = src_dir/a/
        right_base = dst_dir/a/

    Navigating left panel up to src_dir/ (parent of base) should move right
    panel to dst_dir/ (parent of its base).
    """
    left_start = app_ctx.src_dir / "a"
    right_start = app_ctx.dst_dir / "a"
    left_start.mkdir()
    right_start.mkdir()
    fs = app_ctx.fs

    app_ctx.screen._left_panel.set_path(VPath(left_start, fs))
    app_ctx.screen._right_panel.set_path(VPath(right_start, fs))
    await app_ctx.pilot.pause()
    enable_sync(app_ctx)

    # Navigate left panel UP to src_dir/ — one level above the base
    app_ctx.screen._left_panel.set_path(VPath(app_ctx.src_dir, fs))
    await app_ctx.pilot.pause()

    assert app_ctx.screen._right_panel.path.path == PurePosixPath(app_ctx.dst_dir)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_browsing_does_not_echo_back_to_source(app_ctx: AppCtx) -> None:
    """Mirroring the other panel must not trigger a second navigation on the source.

    The source panel must remain at the new path, not bounce back.
    """
    (app_ctx.src_dir / "sub").mkdir()
    (app_ctx.dst_dir / "sub").mkdir()
    await set_panels(app_ctx)
    enable_sync(app_ctx)

    target = VPath(app_ctx.src_dir / "sub", app_ctx.fs)
    app_ctx.screen._left_panel.set_path(target)
    await app_ctx.pilot.pause()

    assert app_ctx.screen._left_panel.path.path == PurePosixPath(app_ctx.src_dir / "sub")


# ---------------------------------------------------------------------------
# Sync state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_browsing_toggle_stores_bases(app_ctx: AppCtx) -> None:
    """Enabling sync browsing captures both panels' current paths as bases."""
    await set_panels(app_ctx)
    enable_sync(app_ctx)

    assert app_ctx.screen._sync_state is not None
    assert app_ctx.screen._sync_state.left_base.path == PurePosixPath(app_ctx.src_dir)
    assert app_ctx.screen._sync_state.right_base.path == PurePosixPath(app_ctx.dst_dir)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_browsing_manual_disable_clears_state(app_ctx: AppCtx) -> None:
    """Manually toggling sync off clears _sync_state and unchecks the menu action."""
    await set_panels(app_ctx)
    enable_sync(app_ctx)
    disable_sync(app_ctx)

    assert app_ctx.screen._sync_state is None
    assert not app_ctx.screen._act("view.sync_browsing").checked


# ---------------------------------------------------------------------------
# Auto-disable on filesystem change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_browsing_disabled_on_filesystem_change(app_ctx: AppCtx) -> None:
    """Navigating the active panel to a different filesystem disables sync.

    We simulate a foreign filesystem by creating a second LocalFilesystem-like
    object that is a different instance, so the identity check fails.
    """
    await set_panels(app_ctx)
    enable_sync(app_ctx)

    # Create a VPath that uses a *different* Filesystem instance
    other_fs = MagicMock(spec=LocalFilesystem)
    other_fs.__class__ = LocalFilesystem
    foreign_path = VPath(app_ctx.src_dir / "anywhere", other_fs)

    app_ctx.screen._left_panel.set_path(foreign_path)
    await app_ctx.pilot.pause()

    assert app_ctx.screen._sync_state is None
    assert not app_ctx.screen._act("view.sync_browsing").checked


# ---------------------------------------------------------------------------
# Rollback when mirror target does not exist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_browsing_rolls_back_panel_when_mirror_missing(app_ctx: AppCtx) -> None:
    """If the mirror path doesn't exist the source panel is rolled back.

    src_dir/sub/ exists but dst_dir/sub/ does NOT.  Navigating left panel into
    src_dir/sub/ should be blocked: left panel returns to src_dir/.
    """
    (app_ctx.src_dir / "sub").mkdir()
    # intentionally do NOT create dst_dir/sub
    await set_panels(app_ctx)
    enable_sync(app_ctx)

    app_ctx.screen._left_panel.set_path(VPath(app_ctx.src_dir / "sub", app_ctx.fs))
    await poll_until(
        app_ctx.pilot,
        lambda: app_ctx.screen._left_panel.path.path == PurePosixPath(app_ctx.src_dir),
    )

    assert app_ctx.screen._left_panel.path.path == PurePosixPath(app_ctx.src_dir)
    assert app_ctx.screen._right_panel.path.path == PurePosixPath(app_ctx.dst_dir)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_browsing_rolls_back_terminal_when_mirror_missing(app_ctx: AppCtx) -> None:
    """If the mirror path doesn't exist the terminal is also rolled back.

    The source panel rollback calls request_cd(prev_path) on the terminal so
    the shell follows the panel back.
    """
    (app_ctx.src_dir / "sub").mkdir()
    # intentionally do NOT create dst_dir/sub
    await set_panels(app_ctx)
    enable_sync(app_ctx)

    with patch.object(app_ctx.screen._terminal_pool.active_terminal, "request_cd") as mock_cd:
        app_ctx.screen._left_panel.set_path(VPath(app_ctx.src_dir / "sub", app_ctx.fs))
        await poll_until(
            app_ctx.pilot,
            lambda: app_ctx.screen._left_panel.path.path == PurePosixPath(app_ctx.src_dir),
        )

    # The terminal should have been asked to go back to src_dir
    mock_cd.assert_called_with(PurePosixPath(app_ctx.src_dir))


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_browsing_terminal_nav_rolls_back_on_missing_mirror(app_ctx: AppCtx) -> None:
    """Terminal-driven navigation also rolls back when mirror is missing.

    Flow: Terminal.PathChanged(user_initiated=True) →
          active_panel().set_path(sub) → PathChanged →
          _mirror_sync detects missing mirror → rollback.
    """
    (app_ctx.src_dir / "sub").mkdir()
    # intentionally do NOT create dst_dir/sub
    await set_panels(app_ctx)
    enable_sync(app_ctx)

    with patch.object(app_ctx.screen._terminal_pool.active_terminal, "request_cd") as mock_cd:
        terminal = app_ctx.screen._terminal_pool.active_terminal
        # Simulate user typing "cd src_dir/sub" in the terminal
        terminal.post_message(
            Terminal.PathChanged(
                terminal,
                PurePosixPath(app_ctx.src_dir / "sub"),
                user_initiated=True,
            )
        )
        await poll_until(
            app_ctx.pilot,
            lambda: app_ctx.screen._left_panel.path.path == PurePosixPath(app_ctx.src_dir),
        )

    # Panel rolled back
    assert app_ctx.screen._left_panel.path.path == PurePosixPath(app_ctx.src_dir)
    # Terminal also rolled back
    mock_cd.assert_called_with(PurePosixPath(app_ctx.src_dir))
