"""SSH integration tests: browsing a remote directory via the Nova Navigator UI.

Each test launches a real NovaNavigator app connected to the in-process stub
SSH server.  File system state is set up directly on the OS (the stub server
uses the real local filesystem), so tests can pre-populate the remote
directory without going through the VFS.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from nova_navigator.vfs import VPath
from tests.integration.conftest import poll_until
from tests.integration.ssh.conftest import SshAppCtx, set_ssh_panels_remote_left

# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_remote_panel_lists_regular_file(ssh_app_ctx: SshAppCtx) -> None:
    """The right panel loads and displays a file that exists on the remote SSH dir."""
    (ssh_app_ctx.remote_dir / "hello.txt").write_text("content")

    ssh_app_ctx.screen._right_panel.set_path(VPath(ssh_app_ctx.remote_dir, ssh_app_ctx.ssh_fs))
    await poll_until(ssh_app_ctx.pilot, lambda: any(
        item.name == "hello.txt" for item in ssh_app_ctx.screen._right_panel._shown_items
    ))

    names = [item.name for item in ssh_app_ctx.screen._right_panel._shown_items]
    assert "hello.txt" in names


@pytest.mark.asyncio
@pytest.mark.integration
async def test_remote_panel_lists_directory(ssh_app_ctx: SshAppCtx) -> None:
    """Subdirectories inside the remote dir appear in the panel listing."""
    (ssh_app_ctx.remote_dir / "subdir").mkdir()

    ssh_app_ctx.screen._right_panel.set_path(VPath(ssh_app_ctx.remote_dir, ssh_app_ctx.ssh_fs))
    await poll_until(ssh_app_ctx.pilot, lambda: any(
        item.name == "subdir" for item in ssh_app_ctx.screen._right_panel._shown_items
    ))

    names = [item.name for item in ssh_app_ctx.screen._right_panel._shown_items]
    assert "subdir" in names


@pytest.mark.asyncio
@pytest.mark.integration
async def test_remote_panel_lists_dotfile(ssh_app_ctx: SshAppCtx) -> None:
    """Hidden files (dot-files) appear in the remote panel listing when show_hidden is enabled."""
    (ssh_app_ctx.remote_dir / ".hidden").write_text("secret")

    ssh_app_ctx.screen._right_panel.show_hidden_files = True
    ssh_app_ctx.screen._right_panel.set_path(VPath(ssh_app_ctx.remote_dir, ssh_app_ctx.ssh_fs))
    await poll_until(ssh_app_ctx.pilot, lambda: any(
        item.name == ".hidden" for item in ssh_app_ctx.screen._right_panel._shown_items
    ))

    names = [item.name for item in ssh_app_ctx.screen._right_panel._shown_items]
    assert ".hidden" in names


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_enter_on_remote_subdir_navigates_into_it(ssh_app_ctx: SshAppCtx) -> None:
    """Pressing Enter on a remote subdirectory navigates the panel into it."""
    subdir = ssh_app_ctx.remote_dir / "subdir"
    subdir.mkdir()

    ssh_app_ctx.screen._left_panel.set_path(VPath(ssh_app_ctx.remote_dir, ssh_app_ctx.ssh_fs))
    ssh_app_ctx.screen._left_panel.focus()
    await poll_until(ssh_app_ctx.pilot, lambda: len(ssh_app_ctx.screen._left_panel._shown_items) > 1)
    await ssh_app_ctx.pilot.press("down")  # land on "subdir"
    await ssh_app_ctx.pilot.pause()

    await ssh_app_ctx.pilot.press("enter")
    await poll_until(ssh_app_ctx.pilot,
        lambda: ssh_app_ctx.screen._left_panel.path.path == PurePosixPath(subdir))

    assert ssh_app_ctx.screen._left_panel.path.path == PurePosixPath(subdir)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_enter_on_dotdot_navigates_to_parent(ssh_app_ctx: SshAppCtx) -> None:
    """Pressing Enter on the '..' entry navigates from a remote subdirectory up to its parent."""
    subdir = ssh_app_ctx.remote_dir / "subdir"
    subdir.mkdir()

    # Start inside the subdirectory; the cursor defaults to row 0 ("..")
    ssh_app_ctx.screen._left_panel.set_path(VPath(subdir, ssh_app_ctx.ssh_fs))
    ssh_app_ctx.screen._left_panel.focus()
    await ssh_app_ctx.pilot.pause()

    # Row 0 is always the ".." (UpPath) entry; Enter selects it and navigates up
    await ssh_app_ctx.pilot.press("enter")
    await poll_until(ssh_app_ctx.pilot,
        lambda: ssh_app_ctx.screen._left_panel.path.path == PurePosixPath(ssh_app_ctx.remote_dir))

    assert ssh_app_ctx.screen._left_panel.path.path == PurePosixPath(ssh_app_ctx.remote_dir)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_left_panel_remote_right_panel_local(ssh_app_ctx: SshAppCtx) -> None:
    """Both panels can be set: left=remote, right=local without errors."""
    (ssh_app_ctx.remote_dir / "remote_file.txt").write_text("from remote")

    await set_ssh_panels_remote_left(ssh_app_ctx)

    names = [item.name for item in ssh_app_ctx.screen._left_panel._shown_items]
    assert "remote_file.txt" in names
