"""The built-in editor saves a file on a real OpenSSH server through its local copy (gnu and busybox servers)."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import paramiko
import pytest

from nova_navigator.local_copies.manager import CopyStatus
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.local_copy_helpers import ScriptedRunner
from tests.integration.conftest import AppCtx, poll_until
from tests.integration.test_editor_integration import back_in_the_list, install_manager, only_entry, open_in_editor
from tests.real_servers.docker import ServerUnavailable, unavailable
from tests.real_servers.ssh import LOOPBACK, SshServer, ssh_servers

pytestmark = [pytest.mark.real_server, pytest.mark.integration, pytest.mark.ssh]


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


def _server(flavour: str) -> SshServer:
    try:
        return ssh_servers()[flavour]
    except ServerUnavailable as exc:
        unavailable(str(exc), os.environ)


@pytest.mark.asyncio
@pytest.mark.parametrize("flavour", ["gnu", "busybox"])
async def test_editor_saves_a_remote_file(app_ctx: AppCtx, tmp_path: Path, flavour: str) -> None:
    server = _server(flavour)
    remote = f"{server.home}/edit-{secrets.token_hex(6)}.txt"
    server.run('cat > "$1"', remote, input=b"remote\n")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    fs = SSHFilesystem(hostname=LOOPBACK, port=server.port, username="nova", password=server.password, ssh_client=client)
    try:
        runner = ScriptedRunner()
        install_manager(app_ctx, tmp_path, runner)
        screen = await open_in_editor(app_ctx, VPath(remote, fs))
        entry = only_entry(app_ctx.app.local_copies)
        assert screen.document.editor.text == "remote\n"

        await app_ctx.pilot.press("x", "ctrl+s")
        await poll_until(app_ctx.pilot, lambda: server.run('cat -- "$1"', remote) == b"xremote\n")
        await poll_until(app_ctx.pilot, lambda: entry.status is CopyStatus.SYNCED)

        await app_ctx.pilot.press("ctrl+w")
        await back_in_the_list(app_ctx)
        assert server.run('cat -- "$1"', remote) == b"xremote\n"
    finally:
        client.close()
