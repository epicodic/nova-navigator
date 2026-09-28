"""Integration tests for the interplay between the directory browser and the terminal.

Two directions are tested:

1. Panel → Terminal: navigating the panel to a new directory triggers a
   ``request_cd`` on the terminal so the shell follows.

2. Terminal → Panel: a user-initiated ``cd`` in the shell (signalled via a
   ``Terminal.PathChanged`` message with ``owner=None``) causes the
   active panel to navigate to the new directory.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock, MagicMock, patch
from zipfile import ZipFile

import pytest

from nova_navigator.terminal import Terminal
from nova_navigator.terminal.vfs_shell import VirtualPtyBackend
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import ArchiveFilesystem
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

    mock_cd.assert_called_once_with(PurePosixPath(subdir))


# ---------------------------------------------------------------------------
# Terminal → Panel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_user_cd_in_terminal_updates_active_panel(app_ctx: AppCtx) -> None:
    """A user-initiated cd in the terminal navigates the active panel.

    Flow: Terminal.PathChanged(owner=None) →
          MainScreen._on_terminal_path_changed → active_panel().set_path
    """
    target = app_ctx.dst_dir  # a real directory distinct from the panel's current path
    await set_panels(app_ctx)

    terminal = app_ctx.screen._terminal_pool.active_terminal
    terminal.post_message(Terminal.PathChanged(terminal, PurePosixPath(target), owner=None))
    await poll_until(app_ctx.pilot, lambda: app_ctx.screen.active_panel().path.path == PurePosixPath(target))

    assert app_ctx.screen.active_panel().path.path == PurePosixPath(target)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_programmatic_cd_does_not_update_panel(app_ctx: AppCtx) -> None:
    """A programmatic navigation via request_cd never updates the active panel.

    Flow: terminal.request_cd(...) starts a hidden transaction handled entirely
    by the ``_nav_busy`` branch of ``Terminal._handle_pre_cmd``, which never
    posts ``Terminal.PathChanged`` — so ``MainScreen._on_terminal_path_changed``
    is never invoked and the panel cannot move.
    """
    # Let the panels' default startup listing (the real cwd) finish before navigating,
    # so its late completion cannot reissue a stray terminal sync afterwards.
    await app_ctx.pilot.pause(delay=1.5)
    await set_panels(app_ctx)
    original_path = app_ctx.screen.active_panel().path.path
    target = PurePosixPath(app_ctx.dst_dir)

    terminal = app_ctx.screen._terminal_pool.active_terminal
    terminal.request_cd(target)
    await poll_until(app_ctx.pilot, lambda: terminal._cwd == target)

    assert terminal._cwd == target
    assert app_ctx.screen.active_panel().path.path == original_path


# ---------------------------------------------------------------------------
# Auto-provisioning: new filesystem → terminal created automatically
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_archive_panel_switches_to_virtual_terminal_and_reuses_it(app_ctx: AppCtx, tmp_path: Path) -> None:
    archive_path = tmp_path / "sample.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("notes.txt", "archive contents\n")

    await set_panels(app_ctx)
    pool = app_ctx.screen._terminal_pool
    local_terminal = pool.active_terminal
    archive_fs = ArchiveFilesystem(VPath(tmp_path, app_ctx.fs), VPath(archive_path, app_ctx.fs))
    archive_root = archive_fs.root()

    app_ctx.screen._left_panel.set_path(archive_root)
    await poll_until(app_ctx.pilot, lambda: pool.active_terminal is not local_terminal)
    archive_terminal = pool.active_terminal
    assert isinstance(archive_terminal._backend, VirtualPtyBackend)
    assert pool.terminal_for(archive_fs) is archive_terminal

    await archive_terminal._backend._run_command("cd ..")
    await app_ctx.pilot.pause()
    assert archive_terminal._backend._interpreter is not None
    assert archive_terminal._backend._interpreter.cwd == archive_root
    assert app_ctx.screen._left_panel.path == archive_root

    app_ctx.screen._left_panel.set_path(archive_root.parent)
    await poll_until(app_ctx.pilot, lambda: pool.active_terminal is local_terminal)
    assert pool.active_terminal is local_terminal

    app_ctx.screen._left_panel.set_path(archive_root)
    await poll_until(app_ctx.pilot, lambda: pool.active_terminal is archive_terminal)
    assert pool.active_terminal is archive_terminal


@pytest.mark.asyncio
@pytest.mark.integration
async def test_archive_virtual_terminal_reads_members(app_ctx: AppCtx, tmp_path: Path) -> None:
    archive_path = tmp_path / "sample.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("notes.txt", "first line\nlast line\n")

    await set_panels(app_ctx)
    archive_fs = ArchiveFilesystem(VPath(tmp_path, app_ctx.fs), VPath(archive_path, app_ctx.fs))
    app_ctx.screen._left_panel.set_path(archive_fs.root())
    pool = app_ctx.screen._terminal_pool
    await poll_until(app_ctx.pilot, lambda: pool.active_terminal is pool.terminal_for(archive_fs))
    backend = pool.active_terminal._backend
    assert isinstance(backend, VirtualPtyBackend)

    for command, expected in (("cat", "first line"), ("head", "first line"), ("tail", "last line")):
        with patch.object(backend, "_post_stdout") as output:
            await backend._run_command(f"{command} notes.txt")
        assert expected in "".join(call.args[0] for call in output.call_args_list)


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
