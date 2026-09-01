"""Integration tests for the interplay between the directory browser and the terminal.

Two directions are tested:

1. Panel → Terminal: navigating the panel to a new directory triggers a
   ``request_cd`` on the terminal so the shell follows.

2. Terminal → Panel: a user-initiated ``cd`` in the shell (signalled via a
   ``Terminal.PathChanged`` message with ``user_initiated=True``) causes the
   active panel to navigate to the new directory.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_navigator.terminal import Terminal
from nova_navigator.vfs import VPath
from tests.integration.conftest import AppCtx, poll_until, set_panels

# ---------------------------------------------------------------------------
# Panel → Terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_panel_navigation_triggers_terminal_request_cd(app_ctx: AppCtx) -> None:
    """Navigating the panel to a new directory calls request_cd on the terminal.

    Flow: set_path → DirectoryBrowser.PathChanged →
          MainScreen._on_directory_browser_path_changed →
          _set_terminal_directory → terminal.request_cd
    """
    subdir = app_ctx.src_dir / "subdir"
    subdir.mkdir()
    await set_panels(app_ctx)

    with patch.object(app_ctx.screen._terminal_pool.active_terminal, "request_cd") as mock_cd:
        app_ctx.screen._left_panel.set_path(VPath(subdir, app_ctx.fs))
        await app_ctx.pilot.pause()

    mock_cd.assert_called_once_with(PurePosixPath(subdir), owner=app_ctx.screen._left_panel)


# ---------------------------------------------------------------------------
# Terminal → Panel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_user_cd_in_terminal_updates_active_panel(app_ctx: AppCtx) -> None:
    """A user-initiated cd in the terminal navigates the active panel.

    Flow: Terminal.PathChanged(user_initiated=True) →
          MainScreen._on_terminal_path_changed → active_panel().set_path
    """
    target = app_ctx.dst_dir  # a real directory distinct from the panel's current path
    await set_panels(app_ctx)

    terminal = app_ctx.screen._terminal_pool.active_terminal
    terminal.post_message(Terminal.PathChanged(terminal, PurePosixPath(target), user_initiated=True))
    await poll_until(app_ctx.pilot, lambda: app_ctx.screen.active_panel().path.path == PurePosixPath(target))

    assert app_ctx.screen.active_panel().path.path == PurePosixPath(target)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_programmatic_cd_does_not_update_panel(app_ctx: AppCtx) -> None:
    """A programmatic cd (user_initiated=False) does not change the active panel.

    Flow: Terminal.PathChanged(user_initiated=False) →
          MainScreen._on_terminal_path_changed → handler exits early
    """
    await set_panels(app_ctx)
    original_path = app_ctx.screen.active_panel().path.path

    terminal = app_ctx.screen._terminal_pool.active_terminal
    terminal.post_message(
        Terminal.PathChanged(
            terminal,
            PurePosixPath(app_ctx.dst_dir),
            user_initiated=False,
        )
    )
    await app_ctx.pilot.pause(delay=0.2)

    assert app_ctx.screen.active_panel().path.path == original_path


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delayed_user_cwd_change_targets_submitting_pane_not_active_pane(app_ctx: AppCtx) -> None:
    """A CWD change owned by the left pane targets it even if focus moved away first.

    Flow: left pane submits a command (owner recorded) → focus switches to the
          right pane → the shell's precmd notification arrives afterwards →
          MainScreen._on_terminal_path_changed routes it to the left pane.
    """
    target = app_ctx.src_dir / "subdir"
    target.mkdir()
    await set_panels(app_ctx)
    left_panel = app_ctx.screen._left_panel
    right_panel = app_ctx.screen._right_panel
    terminal = app_ctx.screen._terminal_pool.active_terminal
    right_panel_path_before = right_panel.path.path

    right_panel.focus()
    await app_ctx.pilot.pause()
    assert app_ctx.screen.active_panel() is right_panel

    terminal.post_message(Terminal.PathChanged(terminal, PurePosixPath(target), user_initiated=True, owner=left_panel))
    await poll_until(app_ctx.pilot, lambda: left_panel.path.path == PurePosixPath(target))

    assert left_panel.path.path == PurePosixPath(target)
    assert right_panel.path.path == right_panel_path_before


@pytest.mark.asyncio
@pytest.mark.integration
async def test_user_cwd_change_with_no_owner_falls_back_to_active_panel(app_ctx: AppCtx) -> None:
    """A user-initiated CWD change with no recorded owner targets the active panel.

    Covers commands entered directly (e.g. while the terminal is maximized) where
    no pane ever called ``submit_enter``, so no owner was recorded.
    """
    await set_panels(app_ctx)
    terminal = app_ctx.screen._terminal_pool.active_terminal

    target = app_ctx.dst_dir
    terminal.post_message(Terminal.PathChanged(terminal, PurePosixPath(target), user_initiated=True, owner=None))
    await poll_until(app_ctx.pilot, lambda: app_ctx.screen.active_panel().path.path == PurePosixPath(target))

    assert app_ctx.screen.active_panel().path.path == PurePosixPath(target)


# ---------------------------------------------------------------------------
# Pane Enter delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pane_enter_consumed_by_terminal_does_not_navigate_panel(app_ctx: AppCtx) -> None:
    """When submit_enter() reports the key was consumed, the pane's own Enter handling does not run."""
    subdir = app_ctx.src_dir / "subdir"
    subdir.mkdir()
    await set_panels(app_ctx)

    # The freshly-spawned real shell's own startup precmd reports its actual process
    # cwd (not src_dir) and, being unowned, would clobber the active panel if it landed
    # later in this test. Wait for it to land first, then re-affirm the panel path.
    terminal = app_ctx.screen._terminal_pool.active_terminal
    await poll_until(app_ctx.pilot, lambda: terminal._at_prompt)
    app_ctx.screen._left_panel.set_path(VPath(app_ctx.src_dir, app_ctx.fs))
    await poll_until(app_ctx.pilot, lambda: app_ctx.screen._left_panel.path.path == app_ctx.src_dir)

    await app_ctx.pilot.press("ctrl+l")  # ENLARGED: terminal visible, not minimized
    await app_ctx.pilot.pause()

    with patch.object(terminal, "submit_enter", AsyncMock(return_value=True)) as mock_submit:
        await app_ctx.pilot.press("enter")
        await app_ctx.pilot.pause(delay=0.2)

    mock_submit.assert_called_once_with(app_ctx.screen._left_panel)
    assert app_ctx.screen._left_panel.path.path == PurePosixPath(app_ctx.src_dir)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pane_enter_not_consumed_by_terminal_navigates_panel(app_ctx: AppCtx) -> None:
    """When submit_enter() reports the key was not consumed, normal pane Enter handling proceeds."""
    subdir = app_ctx.src_dir / "subdir"
    subdir.mkdir()
    await set_panels(app_ctx)

    # See test_pane_enter_consumed_by_terminal_does_not_navigate_panel: wait past the
    # real shell's startup precmd race before proceeding.
    terminal = app_ctx.screen._terminal_pool.active_terminal
    await poll_until(app_ctx.pilot, lambda: terminal._at_prompt)
    app_ctx.screen._left_panel.set_path(VPath(app_ctx.src_dir, app_ctx.fs))
    await poll_until(app_ctx.pilot, lambda: app_ctx.screen._left_panel.path.path == app_ctx.src_dir)

    await app_ctx.pilot.press("ctrl+l")
    await app_ctx.pilot.pause()

    with patch.object(terminal, "submit_enter", AsyncMock(return_value=False)) as mock_submit:
        await app_ctx.pilot.press("enter")
        await poll_until(app_ctx.pilot, lambda: app_ctx.screen._left_panel.path.path == subdir)

    mock_submit.assert_called_once_with(app_ctx.screen._left_panel)
    assert app_ctx.screen._left_panel.path.path == subdir


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pane_enter_while_minimized_does_not_probe_terminal(app_ctx: AppCtx) -> None:
    """While the terminal is minimized, Enter navigates the pane directly without calling submit_enter."""
    subdir = app_ctx.src_dir / "subdir"
    subdir.mkdir()
    await set_panels(app_ctx)
    assert app_ctx.screen._terminal_mode == app_ctx.screen._TerminalMode.MINIMIZED

    terminal = app_ctx.screen._terminal_pool.active_terminal
    with patch.object(terminal, "submit_enter", AsyncMock(return_value=True)) as mock_submit:
        await app_ctx.pilot.press("enter")
        await poll_until(app_ctx.pilot, lambda: app_ctx.screen._left_panel.path.path == subdir)

    mock_submit.assert_not_called()
    assert app_ctx.screen._left_panel.path.path == subdir


# ---------------------------------------------------------------------------
# Auto-provisioning: new filesystem → terminal created automatically
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ensure_terminal_for_provisions_new_filesystem(app_ctx: AppCtx) -> None:
    """_ensure_terminal_for creates and registers a terminal for an unknown filesystem.

    Flow: _ensure_terminal_for → create_for → mount → register
    """
    await set_panels(app_ctx)

    mock_fs = MagicMock()
    mock_fs.unwrap.return_value = mock_fs
    mock_path = VPath(PurePosixPath("/mock"), mock_fs)

    mock_terminal = MagicMock()
    mock_terminal.display = False
    mock_terminal.styles = MagicMock()
    mock_terminal.styles.width = 80
    mock_terminal.styles.height = 24

    factory = AsyncMock(return_value=mock_terminal)
    app_ctx.screen._terminal_pool.register_factory(lambda fs: fs is mock_fs, factory)

    assert not app_ctx.screen._terminal_pool.has_terminal(mock_path.filesystem)

    with patch.object(app_ctx.screen, "mount", new_callable=AsyncMock):
        await app_ctx.screen._ensure_terminal_for(mock_path)

    factory.assert_called_once_with(mock_fs)
    mock_terminal.start.assert_called_once()
    # Terminal is registered before mount (race-safe)
    assert app_ctx.screen._terminal_pool.has_terminal(mock_path.filesystem)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ensure_terminal_for_is_idempotent(app_ctx: AppCtx) -> None:
    """_ensure_terminal_for does nothing when a terminal is already registered."""
    await set_panels(app_ctx)

    mock_fs = MagicMock()
    mock_fs.unwrap.return_value = mock_fs
    mock_path = VPath(PurePosixPath("/mock"), mock_fs)

    factory = AsyncMock(return_value=MagicMock())
    app_ctx.screen._terminal_pool.register_factory(lambda fs: fs is mock_fs, factory)

    with patch.object(app_ctx.screen, "mount", new_callable=AsyncMock):
        await app_ctx.screen._ensure_terminal_for(mock_path)
        await app_ctx.screen._ensure_terminal_for(mock_path)  # second call

    factory.assert_called_once()  # factory only called on first visit


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ensure_terminal_for_no_factory_is_noop(app_ctx: AppCtx) -> None:
    """_ensure_terminal_for does nothing when no factory matches the filesystem."""
    await set_panels(app_ctx)

    mock_fs = MagicMock()
    mock_fs.unwrap.return_value = mock_fs
    mock_path = VPath(PurePosixPath("/mock"), mock_fs)

    # No factory registered for mock_fs

    with patch.object(app_ctx.screen, "mount", new_callable=AsyncMock) as mock_mount:
        await app_ctx.screen._ensure_terminal_for(mock_path)

    mock_mount.assert_not_called()
    assert not app_ctx.screen._terminal_pool.has_terminal(mock_path.filesystem)
