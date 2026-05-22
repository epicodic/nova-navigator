"""SSH PTY backend for the Terminal widget.

Implements ``PtyBackend`` using a paramiko SSH channel with PTY allocation.
The caller provides an authenticated ``paramiko.SSHClient``; this backend
does not manage the SSH connection lifecycle.

Threading model:
A single daemon thread reads from the blocking ``channel.recv()`` and
forwards data into the asyncio ``recv_queue`` via ``call_soon_threadsafe``.
``add_reader`` cannot be used because paramiko channels are not OS fds.

CWD tracking is implemented via in-band OSC 7 escape sequences.
The inherited ``PtyBackend._process_chunk`` scans stdout, strips OSC 7
payloads, and posts ``["pre_cmd", path]`` messages alongside the clean
``["stdout", ...]`` messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

import paramiko

from nova_navigator.terminal.pty_backend import PtyBackend

_logger = logging.getLogger(__name__)

__all__ = ["SshPtyBackend"]


class SshPtyBackend(PtyBackend):
    """PTY backend that runs a shell over an SSH channel.

    Args:
        ssh_client: An authenticated, connected :class:`paramiko.SSHClient`.
            Ownership stays with the caller; ``teardown()`` does not close it.
    """

    def __init__(self, ssh_client: paramiko.SSHClient) -> None:
        super().__init__()
        self._ssh_client = ssh_client
        self._channel: paramiko.Channel | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._recv_queue: asyncio.Queue[list[object]] | None = None
        self._stop_event: threading.Event = threading.Event()
        self._reader_thread: threading.Thread | None = None

    def open(self, command: str, rows: int, cols: int) -> int | None:
        """Open a PTY session channel and start the shell.

        Args:
            command: Shell command to run (e.g. ``"/bin/sh"`` or ``"/usr/bin/zsh"``).
            rows: Initial terminal height.
            cols: Initial terminal width.

        Returns:
            Always ``None`` — no out-of-band precmd pipe for SSH.
        """
        transport = self._ssh_client.get_transport()
        assert transport is not None, "SSH transport is not available"
        self._channel = transport.open_session()
        self._channel.get_pty(term="xterm-256color", width=cols, height=rows)
        self._channel.invoke_shell()
        self._stop_event.clear()
        return None

    def write(self, data: bytes) -> None:
        """Write raw bytes to the remote shell's stdin."""
        assert self._channel is not None
        self._channel.send(data)

    def resize(self, rows: int, cols: int) -> None:
        """Resize the remote PTY."""
        if self._channel is not None:
            with contextlib.suppress(OSError, paramiko.SSHException):
                self._channel.resize_pty(width=cols, height=rows)

    def resume(self) -> None:
        """No-op — no SIGSTOP synchronisation over SSH."""

    def attach_readers(
        self,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        """Start the reader thread that forwards channel output to *recv_queue*."""
        self._loop = loop
        self._recv_queue = recv_queue
        channel = self._channel
        assert channel is not None
        stop_event = self._stop_event

        def _read_loop() -> None:
            while not stop_event.is_set():
                try:
                    data = channel.recv(65536)
                except (OSError, paramiko.SSHException):
                    if not stop_event.is_set():
                        loop.call_soon_threadsafe(recv_queue.put_nowait, ["disconnect", 1])
                    return
                if not data:
                    if not stop_event.is_set():
                        loop.call_soon_threadsafe(recv_queue.put_nowait, ["disconnect", 1])
                    return
                self._process_chunk(data, loop, recv_queue)

        self._reader_thread = threading.Thread(
            target=_read_loop,
            daemon=True,
            name="ssh-pty-reader",
        )
        self._reader_thread.start()

    def detach_readers(self) -> None:
        """Signal the reader thread to stop without generating a disconnect message."""
        self._stop_event.set()
        if self._channel is not None:
            with contextlib.suppress(OSError, paramiko.SSHException):
                self._channel.close()

    def teardown(self) -> None:
        """Close the SSH channel. Does not close the underlying SSHClient."""
        if self._channel is not None:
            with contextlib.suppress(OSError, paramiko.SSHException):
                self._channel.close()
            self._channel = None
