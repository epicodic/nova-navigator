"""SSH integration test: ``write_atomic`` against the real SFTP stack.

Uses the same in-process stub SSH server as the neighbouring SSH integration
tests (``ssh_app_ctx`` in ``tests/integration/ssh/conftest.py``).  Exercises
``SSHFilesystem`` directly rather than through the UI, since ``write_atomic``
is a VFS-level operation with no dedicated keybinding.
"""

from __future__ import annotations

import pytest

from nova_navigator.vfs import VPath
from tests.integration.ssh.conftest import SshAppCtx


@pytest.mark.asyncio
@pytest.mark.integration
async def test_write_atomic_replaces_remote_file_without_leaving_tmp(ssh_app_ctx: SshAppCtx) -> None:
    """write/close replaces the target file over SFTP and leaves no ``.nn-tmp`` sibling."""
    target = ssh_app_ctx.remote_dir / "config.toml"
    target.write_text("old content")
    path = VPath(target, ssh_app_ctx.ssh_fs)

    writer = ssh_app_ctx.ssh_fs.write_atomic(path)
    writer.write(b"new content")
    writer.close()

    reader = ssh_app_ctx.ssh_fs.read(path)
    try:
        assert reader.read(1024) == b"new content"
    finally:
        reader.close()
    assert target.read_text() == "new content"

    names = {item.name async for item in ssh_app_ctx.ssh_fs.iterdir(VPath(ssh_app_ctx.remote_dir, ssh_app_ctx.ssh_fs))}
    assert names == {"config.toml"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_write_atomic_abort_leaves_remote_file_and_tmp_untouched(ssh_app_ctx: SshAppCtx) -> None:
    """abort() discards the upload and leaves no ``.nn-tmp`` sibling behind."""
    target = ssh_app_ctx.remote_dir / "config.toml"
    target.write_text("old content")
    path = VPath(target, ssh_app_ctx.ssh_fs)

    writer = ssh_app_ctx.ssh_fs.write_atomic(path)
    writer.write(b"partial upload")
    writer.abort()

    assert target.read_text() == "old content"
    names = {item.name async for item in ssh_app_ctx.ssh_fs.iterdir(VPath(ssh_app_ctx.remote_dir, ssh_app_ctx.ssh_fs))}
    assert names == {"config.toml"}
