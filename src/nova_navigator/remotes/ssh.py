"""SSH connection establishment with interactive UI dialogs."""

from __future__ import annotations

import asyncio
import logging

import paramiko

from nova_navigator.config.remotes import RemoteConnection
from nova_navigator.dialogs import CredentialsDialog, MessageBox
from nova_navigator.response import Response
from nova_navigator.vfs.filesystems import SSHFilesystem, UnknownHostKeyError

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
