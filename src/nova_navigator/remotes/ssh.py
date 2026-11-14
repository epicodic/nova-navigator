"""SSH connection establishment with interactive UI dialogs."""

from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import paramiko

from nova_navigator.config import conf_
from nova_navigator.config.remotes import RemoteConnection, SshSettings
from nova_navigator.dialogs import CredentialsDialog
from nova_navigator.plugins import FilesystemPlugin
from nova_navigator.remotes.known_hosts import KnownHostsLocations, fingerprint, key_type_name
from nova_navigator.remotes.ssh_config import SshConfig
from nova_navigator.remotes.ssh_route import Explicit, SshHop, SshRouteError, resolve_route
from nova_navigator.terminal.shell_driver import detect_driver
from nova_navigator.terminal.ssh_pty_backend import SshPtyBackend
from nova_navigator.terminal.terminal import Terminal
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems import (
    ChangedHostKeyError,
    ConfirmedKeyMismatchError,
    HopAuthenticationError,
    HostKeyError,
    RevokedHostKeyError,
    SSHFilesystem,
    UnknownHostKeyError,
)
from nova_navigator.vfs.vpath import VPath
from nova_widgets import MessageBox, Response

_logger = logging.getLogger(__name__)

UNKNOWN_TITLE = "Unknown Host"
REVOKED_TITLE = "Revoked Host Key"
CHANGED_TITLE = "Host Key Changed"
MISMATCH_TITLE = "Host Key Mismatch"
SAVE_FAILURE_TITLE = "Host Key Not Saved"


_MIN_TIMEOUT_SECONDS = 1.0


@dataclass
class _Attempt:
    """Everything one connection needs; the dicts grow between attempts, never during one."""

    ssh: SshSettings
    route: list[SshHop]
    username: str | None
    timeout: float
    locations: KnownHostsLocations
    config: SshConfig
    trusted: dict[str, paramiko.PKey] = field(default_factory=dict)
    credentials: dict[int, tuple[str | None, str | None]] = field(default_factory=dict)


def _connect_timeout() -> float:
    """Return ``network.ssh_timeout`` in seconds, read per connection so a changed setting applies to the next one; at least one second."""
    return max(float(conf_.settings.network.ssh_timeout), _MIN_TIMEOUT_SECONDS)


async def connect_ssh(conn: RemoteConnection) -> SSHFilesystem | None:
    """Establish an SSH connection with interactive UI for host-key and credential prompts.

    Resolves ssh_config aliases and ``ProxyJump`` chains (explicit values of *conn* win) and applies
    ``network.ssh_timeout`` to every hop.
    Returns the connected `SSHFilesystem`, or `None` if the user cancelled, the host key was
    refused (changed, revoked or mismatched) or the connection failed.  `conn.ssh` must not be `None`.
    """
    assert conn.ssh is not None
    ssh = conn.ssh
    locations = KnownHostsLocations.default()
    config = await asyncio.to_thread(SshConfig.load, locations.config_paths)
    try:
        route = resolve_route(ssh.host, Explicit(ssh.user, ssh.port, ssh.identity_file), config)
    except SshRouteError as exc:
        _logger.warning("Cannot resolve the SSH route for %r: %s", conn.name, exc)
        await _report_failure(conn.name, exc)
        return None
    username = ssh.user or config.lookup(ssh.host).user
    fs = await _connect(conn.name, _Attempt(ssh, route, username, _connect_timeout(), locations, config))
    if fs is not None and fs.host_key_save_error is not None and fs.host_key_save_path is not None:
        await MessageBox(save_failure_message(fs.host_key_save_path, fs.host_key_save_error), title=SAVE_FAILURE_TITLE, variant="warning").run()
    return fs


async def _connect(name: str, attempt: _Attempt) -> SSHFilesystem | None:
    """Connect; every question (unknown key, credentials) is asked between attempts and the chain restarts afterwards.

    Earlier hops reconnect without new questions: their keys are stored and their typed credentials kept in *attempt*.
    """
    ssh = attempt.ssh
    while True:
        try:
            return await asyncio.to_thread(
                SSHFilesystem,
                ssh.host,
                ssh.port or 22,
                attempt.username,
                None,
                None,
                route=attempt.route,
                connect_timeout=attempt.timeout,
                trusted_host_keys=attempt.trusted,
                credentials=attempt.credentials,
                known_hosts=attempt.locations,
                ssh_config=attempt.config,
            )
        except UnknownHostKeyError as exc:
            _logger.warning("Unknown host key for %r: %s %s", name, exc.key_type, exc.fingerprint)
            confirm = MessageBox(unknown_host_message(exc), title=UNKNOWN_TITLE, buttons=[Response.OK, Response.CANCEL], variant="warning")
            if await confirm.run() != Response.OK:
                return None
            attempt.trusted[exc.hostname] = exc.key
        except HostKeyError as exc:  # revoked, changed or mismatched: refused, there is no way to accept
            _logger.warning("Host key refused for %r: %s %s %s", name, type(exc).__name__, exc.key_type, exc.fingerprint)
            await _show_refusal(exc)
            return None
        except HopAuthenticationError as exc:
            if exc.index in attempt.credentials:  # the typed credentials were wrong: no second prompt
                _logger.warning("SSH password auth failed for hop %d of %r", exc.index, name)
                await _report_failure(name, exc)
                return None
            dialog = CredentialsDialog(exc.hop.hostname, exc.hop.user or "")
            if await dialog.run() != Response.OK:
                return None
            creds = dialog.credentials
            attempt.credentials[exc.index] = (creds.username or None, creds.password or None)
        except Exception as exc:
            _logger.exception("SSH connection error for %r: %s", name, exc)
            await _report_failure(name, exc)
            return None


async def _report_failure(name: str, exc: Exception) -> None:
    """Tell the user the connection failed."""
    await MessageBox(f"Could not connect to {name!r}:\n{exc}", variant="error").run()


def unknown_host_message(exc: UnknownHostKeyError) -> str:
    """Text of the confirmation for a host that is not in known_hosts."""
    return f"The authenticity of host {exc.hostname!r} can't be established.\n{exc.key_type} key fingerprint is {exc.fingerprint}\n\nAdd to known hosts?"


def revoked_host_message(exc: RevokedHostKeyError) -> str:
    """Text of the refusal for a key listed as ``@revoked``."""
    return (
        f"The {exc.key_type} host key presented by {exc.hostname!r} is listed as revoked in known_hosts.\n"
        f"Key fingerprint: {exc.fingerprint}\n\n"
        "The key must not be trusted, so the connection was refused."
    )


def changed_host_message(exc: ChangedHostKeyError) -> str:
    """Text of the warning for a host key that differs from the stored one."""
    return (
        f"WARNING: the host key of {exc.hostname!r} has changed.\n\n"
        f"Presented key: {exc.key_type} {exc.fingerprint}\n"
        f"Stored key:    {key_type_name(exc.expected_key)} {fingerprint(exc.expected_key)}\n\n"
        "The presented key differs from the one stored in known_hosts. "
        "Someone may be intercepting the connection, or the server was reinstalled.\n"
        "The connection was refused and nothing was changed. "
        f"If the change is expected, remove the old entry (ssh-keygen -R {shlex.quote(exc.hostname)}) and connect again."
    )


def mismatch_message(exc: ConfirmedKeyMismatchError) -> str:
    """Text of the refusal when the server presents another key than the confirmed one."""
    return (
        f"The server {exc.hostname!r} presented a different host key than the one you confirmed.\n\n"
        f"Confirmed: {key_type_name(exc.confirmed_key)} {fingerprint(exc.confirmed_key)}\n"
        f"Presented: {exc.key_type} {exc.fingerprint}\n\n"
        "The connection was refused and nothing was saved."
    )


def save_failure_message(path: Path, error: OSError) -> str:
    """Text of the warning shown when the confirmed host key could not be written."""
    return f"Connected, but the host key could not be saved to {path}: {error}.\nYou will be asked again next time."


async def _show_refusal(exc: HostKeyError) -> None:
    """Show the OK-only error box for a refused host key."""
    title, message = _refusal(exc)
    await MessageBox(message, title=title, variant="error").run()


def _refusal(exc: HostKeyError) -> tuple[str, str]:
    """Return ``(title, message)`` for a host-key refusal."""
    if isinstance(exc, RevokedHostKeyError):
        return REVOKED_TITLE, revoked_host_message(exc)
    if isinstance(exc, ConfirmedKeyMismatchError):
        return MISMATCH_TITLE, mismatch_message(exc)
    if isinstance(exc, ChangedHostKeyError):
        return CHANGED_TITLE, changed_host_message(exc)
    return "Host Key Refused", f"The host key of {exc.hostname!r} was refused."


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


def _parse_shell_detection_output(output: str) -> str:
    """Return the first non-blank line of *output*, or ``/bin/sh`` if none.

    Used to pick between ``$SHELL`` and the ``getent passwd`` fallback: the
    first command to produce a non-empty line wins.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "/bin/sh"


async def _make_ssh_terminal(fs: Filesystem) -> Terminal | None:
    """Create a Terminal for *fs* using the detected remote shell.

    Opens a short-lived exec channel to determine whether the remote shell is
    zsh, bash, or a POSIX fallback. ``$SHELL`` is tried first; some sshd
    configurations don't export it to non-interactive exec sessions, so the
    account's configured login shell (via ``getent passwd``) is used as a
    fallback.
    SIGSTOP/SIGCONT is always disabled for remote shells.
    """
    ssh_fs = cast("SSHFilesystem", fs)

    def _detect_shell() -> str:
        try:
            transport = ssh_fs._ssh_client.get_transport()
            if transport is None:
                return "/bin/sh"
            channel = transport.open_session()
            channel.exec_command('echo "$SHELL"; getent passwd "$(whoami)" 2>/dev/null | cut -d: -f7')
            output = channel.recv(256).decode("utf-8", errors="replace")
            channel.close()
            return _parse_shell_detection_output(output)
        except (OSError, paramiko.SSHException, EOFError):
            return "/bin/sh"

    shell_path = await asyncio.to_thread(_detect_shell)
    driver = detect_driver(shell_path)
    return Terminal(
        "ssh",
        backend=SshPtyBackend(ssh_fs._ssh_client),
        driver=driver,
        keep_alive=False,
        scrollback_lines=conf_.settings.terminal.scrollback_lines,
    )


SSH_PLUGIN = FilesystemPlugin(
    scheme="ssh",
    fs_type=SSHFilesystem,
    connector=SshConnector(),
    terminal_factory=_make_ssh_terminal,
)
