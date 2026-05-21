"""SSH connection establishment with interactive UI dialogs."""

from __future__ import annotations

import asyncio
import logging
from typing import cast

import paramiko

from nova_navigator.config.remotes import RemoteConnection, SshSettings
from nova_navigator.dialogs import CredentialsDialog, MessageBox
from nova_navigator.plugins import FilesystemPlugin
from nova_navigator.response import Response
from nova_navigator.terminal.ssh_pty_backend import SshPtyBackend
from nova_navigator.terminal.terminal import Terminal
from nova_navigator.vfs.filesystems import SSHFilesystem, UnknownHostKeyError
from nova_navigator.vfs.vpath import VPath

_logger = logging.getLogger(__name__)


async def connect_ssh(conn: RemoteConnection) -> SSHFilesystem | None:
    """Establish an SSH connection with interactive UI for host-key and credential prompts.

    Returns the connected `SSHFilesystem`, or `None` if the user cancelled or the
    connection failed.  `conn.ssh` must not be `None`.
    """
    assert conn.ssh is not None
    ssh = conn.ssh
    port = ssh.port or 22
    username = ssh.user
    password: str | None = None

    try:
        fs = await asyncio.to_thread(SSHFilesystem, ssh.host, port, username, ssh.identity_file, password)
    except UnknownHostKeyError as exc:
        _logger.warning("Unknown host key for %r: %s %s", conn.name, exc.key_type, exc.fingerprint)
        confirm = MessageBox(
            f"The authenticity of host {exc.hostname!r} can't be established.\n"
            f"{exc.key_type} key fingerprint is {exc.fingerprint}\n\nAdd to known hosts?",
            title="Unknown Host",
            buttons=[Response.OK, Response.CANCEL],
            variant="warning",
        )
        if await confirm.run() != Response.OK:
            return None
        try:
            fs = await asyncio.to_thread(
                SSHFilesystem, ssh.host, port, username, ssh.identity_file, password, accept_host_key=True
            )
        except paramiko.AuthenticationException:
            fs = await _prompt_credentials(ssh.host, port, username)
        except Exception as exc2:
            _logger.exception("SSH connection error for %r: %s", conn.name, exc2)
            await MessageBox(f"Could not connect to {conn.name!r}:\n{exc2}", variant="error").run()
            return None
    except paramiko.AuthenticationException:
        fs = await _prompt_credentials(ssh.host, port, username)
    except Exception as exc:
        _logger.exception("SSH connection error for %r: %s", conn.name, exc)
        await MessageBox(f"Could not connect to {conn.name!r}:\n{exc}", variant="error").run()
        return None

    return fs


async def _prompt_credentials(hostname: str, port: int, username: str | None) -> SSHFilesystem | None:
    """Show the credentials dialog and attempt a password-based SSH connection.

    Returns the connected `SSHFilesystem`, or `None` if the user cancelled or auth failed.
    """
    cred_dialog = CredentialsDialog(hostname, username or "")
    if await cred_dialog.run() != Response.OK:
        return None
    creds = cred_dialog.credentials
    try:
        return await asyncio.to_thread(
            SSHFilesystem, hostname, port, creds.username or None, None, creds.password or None
        )
    except Exception as exc:
        _logger.exception("SSH password auth failed for %r: %s", hostname, exc)
        await MessageBox(f"Could not connect to {hostname!r}:\n{exc}", variant="error").run()
        return None


def _parse_netloc(netloc: str) -> tuple[str, str | None, int | None]:
    """Parse ``[user@]host[:port]`` into ``(host, user, port)``."""
    if "@" in netloc:
        user_part, host_part = netloc.rsplit("@", 1)
        user: str | None = user_part or None
    else:
        user = None
        host_part = netloc

    if ":" in host_part:
        host, port_str = host_part.rsplit(":", 1)
        port: int | None = int(port_str) if port_str.isdigit() else None
    else:
        host = host_part
        port = None

    return host, user, port


class SshConnector:
    """Connector that establishes an SSH connection from an ``ssh://`` URI.

    Parses ``netloc`` as ``[user@]host[:port]`` and delegates to :func:`connect_ssh`,
    which shows interactive UI dialogs for host-key confirmation and credentials.
    Returns ``None`` if the user cancels any dialog.
    """

    async def resolve(self, path: str, netloc: str | None) -> VPath | None:
        host, user, port = _parse_netloc(netloc or "")
        if not host:
            raise ValueError("No hostname in SSH URI")

        conn = RemoteConnection(name=host, ssh=SshSettings(host=host, user=user, port=port))
        fs = await connect_ssh(conn)
        if fs is None:
            return None
        return VPath(path or "/", fs)


SSH_PLUGIN = FilesystemPlugin(
    scheme="ssh",
    fs_type=SSHFilesystem,
    connector=SshConnector(),
    terminal_factory=lambda fs: Terminal(
        "ssh",
        backend=SshPtyBackend(cast("SSHFilesystem", fs)._ssh_client),
        keep_alive=False,
    ),
)
