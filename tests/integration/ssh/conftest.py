"""Shared fixtures for SSH integration tests.

Each test gets its own isolated local and remote directory pair via pytest's
``tmp_path`` fixture.  The stub SSH server is session-scoped so it is started
once and reused across all tests in the session.

Fixtures
--------
ssh_server (session-scoped)
    A running :class:`StubSSHServer` bound to a random localhost port.

ssh_app_ctx (function-scoped)
    Launches a full NovaNavigator app with the left panel pointed at a local
    directory and the right panel at a remote SSH directory.  Yields a
    :class:`SshAppCtx`.

Helpers
-------
set_ssh_panels(ctx)
    Point the left panel at ``ctx.local_dir`` (local filesystem, active) and
    the right panel at ``ctx.remote_dir`` (SSH filesystem).  Waits for both
    panels to finish their initial directory scans.

set_ssh_panels_remote_left(ctx)
    Like ``set_ssh_panels`` but with the remote dir on the left (active) and
    the local dir on the right.  Used for remote-to-local copy tests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import paramiko
import pytest
import pytest_asyncio

from nova_navigator.nova_navigator import MainScreen, NovaNavigator
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem, SSHFilesystem
from tests._utils.stub_ssh_server import StubSSHServer

# ---------------------------------------------------------------------------
# SSH server fixture (session-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def ssh_server(tmp_path_factory: pytest.TempPathFactory) -> StubSSHServer:
    """Start a stub SSH server once for the entire test session."""
    root = tmp_path_factory.mktemp("ssh_root")
    server = StubSSHServer(root_dir=root)
    server.start()
    yield server  # type: ignore[misc]
    server.stop()


# ---------------------------------------------------------------------------
# App context dataclass
# ---------------------------------------------------------------------------


@dataclass
class SshAppCtx:
    """Context passed to every SSH integration test."""

    pilot: object        # textual.pilot.Pilot[None] — kept as `object` to avoid import
    screen: MainScreen
    local_dir: Path
    remote_dir: Path
    ssh_fs: SSHFilesystem


# ---------------------------------------------------------------------------
# Per-test app fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ssh_app_ctx(
    tmp_path: Path,
    ssh_server: StubSSHServer,
    headed: bool,
) -> object:  # yields SshAppCtx
    """Launch NovaNavigator with a local left panel and SSH right panel.

    The SSHFilesystem connection is established in a worker thread to avoid
    blocking the event loop during the TCP/SSH handshake.
    """
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()

    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # noqa: S507

    ssh_fs: SSHFilesystem = await asyncio.to_thread(
        lambda: SSHFilesystem(
            hostname=ssh_server.host,
            port=ssh_server.port,
            username="testuser",
            password="testpass",  # noqa: S106
            ssh_client=ssh_client,
        )
    )

    app = NovaNavigator()
    async with app.run_test(size=(120, 40), headless=not headed) as pilot:
        await pilot.pause()
        screen = app._main_screen
        yield SshAppCtx(
            pilot=pilot,
            screen=screen,
            local_dir=local_dir,
            remote_dir=remote_dir,
            ssh_fs=ssh_fs,
        )

    ssh_fs._ssh_client.close()


# ---------------------------------------------------------------------------
# Panel setup helpers
# ---------------------------------------------------------------------------

_SSH_SCAN_DELAY = 0.5  # seconds: SSH exec_command involves a subprocess, so we wait longer than local


async def set_ssh_panels(ctx: SshAppCtx) -> None:
    """Point left=local (active), right=remote SSH.  Waits for scan and moves cursor off '..'."""
    ctx.screen._left_panel.set_path(VPath(ctx.local_dir, LocalFilesystem.singleton()))
    ctx.screen._right_panel.set_path(VPath(ctx.remote_dir, ctx.ssh_fs))
    ctx.screen._left_panel.focus()
    await ctx.pilot.pause(delay=_SSH_SCAN_DELAY)  # type: ignore[union-attr]
    await ctx.pilot.press("down")  # type: ignore[union-attr]  # skip past ".." to first file
    await ctx.pilot.pause()  # type: ignore[union-attr]


async def set_ssh_panels_remote_left(ctx: SshAppCtx) -> None:
    """Point left=remote SSH (active), right=local.  Waits for scan and moves cursor off '..'."""
    ctx.screen._left_panel.set_path(VPath(ctx.remote_dir, ctx.ssh_fs))
    ctx.screen._right_panel.set_path(VPath(ctx.local_dir, LocalFilesystem.singleton()))
    ctx.screen._left_panel.focus()
    await ctx.pilot.pause(delay=_SSH_SCAN_DELAY)  # type: ignore[union-attr]
    await ctx.pilot.press("down")  # type: ignore[union-attr]
    await ctx.pilot.pause()  # type: ignore[union-attr]
