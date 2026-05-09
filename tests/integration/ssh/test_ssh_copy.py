"""SSH integration tests: file copy (F5) between local and remote filesystems.

Tests cover:
- Local → remote copy (basic file content)
- Local → remote copy with binary data
- Remote → local copy
- Regression test for a known bug:
    SSHFilesystem.stat() returns Stat() for non-existent files instead of
    raising FileNotFoundError.  This causes VPath.stat_or_none() to return a
    non-None Stat() for new destinations, which triggers a spurious overwrite
    dialog on every local→remote copy regardless of whether the destination
    file already exists.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_navigator.response import Response
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem
from tests.integration.conftest import auto_confirm_copy_dialog, auto_confirm_response_dialog, poll_until
from tests.integration.ssh.conftest import SshAppCtx, set_ssh_panels, set_ssh_panels_remote_left

_COPY_DIALOG_PATH = "nova_navigator.filemanager.jobs.CopyMoveFilesDialog"
_RESPONSE_DIALOG_PATH = "nova_navigator.nova_navigator.make_response_dialog"

# Give SSH exec_command (subprocess) time to complete in addition to the copy I/O.
_COPY_WAIT = 1.5


# ---------------------------------------------------------------------------
# Local → remote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_local_to_remote_file_appears(ssh_app_ctx: SshAppCtx) -> None:
    """F5 on a local file copies it to the remote panel directory."""
    (ssh_app_ctx.local_dir / "hello.txt").write_text("content")
    await set_ssh_panels(ssh_app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await ssh_app_ctx.pilot.press("f5")
        await poll_until(
            ssh_app_ctx.pilot,
            lambda: (ssh_app_ctx.remote_dir / "hello.txt").exists()
            and (ssh_app_ctx.remote_dir / "hello.txt").stat().st_size > 0,
        )

    assert (ssh_app_ctx.remote_dir / "hello.txt").exists()
    assert (ssh_app_ctx.remote_dir / "hello.txt").read_text() == "content"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_local_to_remote_binary_content_preserved(ssh_app_ctx: SshAppCtx) -> None:
    """Binary file copied local → remote is byte-identical to the source."""
    data = bytes(range(256)) * 64  # 16 KB of all byte values
    (ssh_app_ctx.local_dir / "binary.bin").write_bytes(data)
    await set_ssh_panels(ssh_app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await ssh_app_ctx.pilot.press("f5")
        await poll_until(
            ssh_app_ctx.pilot,
            lambda: (ssh_app_ctx.remote_dir / "binary.bin").exists()
            and (ssh_app_ctx.remote_dir / "binary.bin").stat().st_size == len(data),
        )

    assert (ssh_app_ctx.remote_dir / "binary.bin").read_bytes() == data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_local_to_remote_large_file(ssh_app_ctx: SshAppCtx) -> None:
    """A file larger than CHUNK_SIZE (64 KB) is copied correctly local → remote."""
    data = b"x" * (200 * 1024)  # 200 KB
    (ssh_app_ctx.local_dir / "large.bin").write_bytes(data)
    await set_ssh_panels(ssh_app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await ssh_app_ctx.pilot.press("f5")
        await poll_until(
            ssh_app_ctx.pilot,
            lambda: (ssh_app_ctx.remote_dir / "large.bin").exists()
            and (ssh_app_ctx.remote_dir / "large.bin").stat().st_size == len(data),
        )

    assert (ssh_app_ctx.remote_dir / "large.bin").read_bytes() == data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_local_to_remote_filename_with_spaces(ssh_app_ctx: SshAppCtx) -> None:
    """A local file whose name contains spaces is correctly copied to remote."""
    (ssh_app_ctx.local_dir / "my file.txt").write_text("spaced")
    await set_ssh_panels(ssh_app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await ssh_app_ctx.pilot.press("f5")
        await poll_until(
            ssh_app_ctx.pilot,
            lambda: (ssh_app_ctx.remote_dir / "my file.txt").exists()
            and (ssh_app_ctx.remote_dir / "my file.txt").stat().st_size > 0,
        )

    assert (ssh_app_ctx.remote_dir / "my file.txt").read_text() == "spaced"


# ---------------------------------------------------------------------------
# Regression test for the known bug: spurious overwrite dialog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_local_to_remote_no_spurious_overwrite_dialog(ssh_app_ctx: SshAppCtx) -> None:
    """Copying to a non-existent remote path must NOT trigger the overwrite dialog."""
    (ssh_app_ctx.local_dir / "newfile.txt").write_text("hello")
    await set_ssh_panels(ssh_app_ctx)

    p_copy = patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog())
    p_dec = patch(_RESPONSE_DIALOG_PATH, return_value=auto_confirm_response_dialog(Response.YES))
    with p_copy, p_dec as mock_response:
        await ssh_app_ctx.pilot.press("f5")
        await poll_until(ssh_app_ctx.pilot, lambda: (ssh_app_ctx.remote_dir / "newfile.txt").exists())

    mock_response.assert_not_called()


# ---------------------------------------------------------------------------
# Remote → local
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_remote_to_local_file_appears(ssh_app_ctx: SshAppCtx) -> None:
    """F5 on a remote file copies it into the local destination directory."""
    (ssh_app_ctx.remote_dir / "remote_file.txt").write_text("from remote")
    await set_ssh_panels_remote_left(ssh_app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await ssh_app_ctx.pilot.press("f5")
        await poll_until(ssh_app_ctx.pilot, lambda: (ssh_app_ctx.local_dir / "remote_file.txt").exists())

    assert (ssh_app_ctx.local_dir / "remote_file.txt").exists()
    assert (ssh_app_ctx.local_dir / "remote_file.txt").read_text() == "from remote"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_remote_to_local_binary_content_preserved(ssh_app_ctx: SshAppCtx) -> None:
    """Binary file copied remote → local is byte-identical to the source."""
    data = bytes(range(256)) * 128
    (ssh_app_ctx.remote_dir / "binary.bin").write_bytes(data)
    await set_ssh_panels_remote_left(ssh_app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await ssh_app_ctx.pilot.press("f5")
        await poll_until(
            ssh_app_ctx.pilot,
            lambda: (ssh_app_ctx.local_dir / "binary.bin").exists()
            and (ssh_app_ctx.local_dir / "binary.bin").stat().st_size == len(data),
        )

    assert (ssh_app_ctx.local_dir / "binary.bin").read_bytes() == data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_cancelled_leaves_remote_destination_empty(ssh_app_ctx: SshAppCtx) -> None:
    """Cancelling the copy dialog leaves the remote destination directory untouched."""
    (ssh_app_ctx.local_dir / "file.txt").write_text("data")
    await set_ssh_panels(ssh_app_ctx)

    cancel_dialog = MagicMock()
    cancel_dialog.run = AsyncMock(return_value="CANCEL")

    with patch(_COPY_DIALOG_PATH, return_value=cancel_dialog):
        await ssh_app_ctx.pilot.press("f5")
        await ssh_app_ctx.pilot.pause(delay=0.5)

    assert list(ssh_app_ctx.remote_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Overwrite existing remote file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_local_to_remote_overwrites_when_confirmed(ssh_app_ctx: SshAppCtx) -> None:
    """When the remote destination already exists and the user confirms, it is overwritten."""
    (ssh_app_ctx.local_dir / "file.txt").write_text("new content")
    (ssh_app_ctx.remote_dir / "file.txt").write_text("old content")
    await set_ssh_panels(ssh_app_ctx)

    p_copy = patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog())
    p_dec = patch(_RESPONSE_DIALOG_PATH, return_value=auto_confirm_response_dialog(Response.YES))
    with p_copy, p_dec:
        await ssh_app_ctx.pilot.press("f5")
        await poll_until(ssh_app_ctx.pilot, lambda: (ssh_app_ctx.remote_dir / "file.txt").read_text() == "new content")

    assert (ssh_app_ctx.remote_dir / "file.txt").read_text() == "new content"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_local_to_remote_skips_when_overwrite_declined(ssh_app_ctx: SshAppCtx) -> None:
    """When the remote destination already exists and the user declines, the original is kept."""
    (ssh_app_ctx.local_dir / "file.txt").write_text("new content")
    (ssh_app_ctx.remote_dir / "file.txt").write_text("old content")

    # For the right panel to see the existing remote file, we need to point it
    # at the remote dir before the panel's stat cache is populated.
    ssh_app_ctx.screen._left_panel.set_path(VPath(ssh_app_ctx.local_dir, LocalFilesystem.singleton()))
    ssh_app_ctx.screen._right_panel.set_path(VPath(ssh_app_ctx.remote_dir, ssh_app_ctx.ssh_fs))
    ssh_app_ctx.screen._left_panel.focus()
    await ssh_app_ctx.pilot.pause(delay=0.5)
    await ssh_app_ctx.pilot.press("down")
    await ssh_app_ctx.pilot.pause()

    # Prime the SSHFilesystem _dir_stat cache so it knows the remote file exists.
    # The stat call populates the lru_cache entry for the remote parent directory.
    remote_vpath = VPath(ssh_app_ctx.remote_dir / "file.txt", ssh_app_ctx.ssh_fs)
    _ = remote_vpath.stat_or_none  # warm the cache

    p_copy = patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog())
    p_dec = patch(_RESPONSE_DIALOG_PATH, return_value=auto_confirm_response_dialog(Response.NO))
    with p_copy, p_dec:
        await ssh_app_ctx.pilot.press("f5")
        await ssh_app_ctx.pilot.pause(delay=_COPY_WAIT)

    assert (ssh_app_ctx.remote_dir / "file.txt").read_text() == "old content"
