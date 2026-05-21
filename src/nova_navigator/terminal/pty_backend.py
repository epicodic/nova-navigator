"""PTY backend abstraction for terminal process management.

This module provides the ``PtyBackend`` ABC and ``LocalPtyBackend`` implementation.
A PtyBackend handles all OS-level concerns of running a shell process:
starting/stopping the process, reading/writing bytes, resizing the terminal,
and managing the precmd pipe for CWD tracking.

The backend does not know anything about shell languages (hooks, quoting) —
that is the responsibility of ``ShellDriver`` in ``shell_driver.py``.

The backend does not know about rendering, draining, or Textual widgets —
that is the responsibility of ``Terminal`` in ``terminal.py``.

Related modules:
- ``shell_driver.py`` — shell-specific hook installation and quoting.
- ``terminal.py`` — Textual widget that consumes backend I/O.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import io
import logging
import os
import pty
import shlex
import signal
import struct
import termios
from abc import ABC, abstractmethod

_logger = logging.getLogger(__name__)


class PtyBackend(ABC):
    """Abstract base class for terminal process backends.

    A PtyBackend manages the lifecycle of a shell process and provides
    byte-level I/O.  It does not interpret the bytes — the Terminal widget
    feeds them to pyte for rendering.

    Lifecycle: ``open()`` → ``attach_readers()`` → (normal operation)
    → ``detach_readers()`` → ``teardown()``.
    """

    @abstractmethod
    def open(self, command: str, rows: int, cols: int) -> int | None:
        """Start the shell process.

        Args:
            command: The shell command to execute.
            rows: Initial terminal height.
            cols: Initial terminal width.

        Returns:
            The precmd pipe child-side fd number, or None if not supported.
        """

    @abstractmethod
    def write(self, data: bytes) -> None:
        """Write raw bytes to the shell's stdin."""

    @abstractmethod
    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal."""

    @abstractmethod
    def resume(self) -> None:
        """Send SIGCONT to the managed shell process.

        Suppresses ProcessLookupError if the shell has exited.
        """

    @abstractmethod
    def attach_readers(
        self,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        """Register callbacks that push stdout/precmd/disconnect into recv_queue.

        Stores the loop reference for detach_readers().
        """

    @abstractmethod
    def detach_readers(self) -> None:
        """Remove previously registered reader callbacks."""

    @abstractmethod
    def teardown(self) -> None:
        """Terminate the shell process and close all file objects."""

    @property
    @abstractmethod
    def supports_precmd_pipe(self) -> bool:
        """True if this backend has an out-of-band precmd pipe."""


class LocalPtyBackend(PtyBackend):
    """PTY backend for local shell processes.

    Uses ``pty.fork()`` to create a pseudo-terminal and ``os.pipe()`` for the
    out-of-band precmd communication channel.
    """

    def __init__(self) -> None:
        self._pid: int = -1
        self._master_fd: int = -1
        self._p_out: io.FileIO | None = None
        self._p_out_pre_cmd: io.FileIO | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def supports_precmd_pipe(self) -> bool:
        return True

    def open(self, command: str, rows: int, cols: int) -> int | None:
        fd_pre_cmd_parent, fd_pre_cmd_child = os.pipe()

        pid, fd = pty.fork()
        if pid == 0:
            # Child process
            os.close(fd_pre_cmd_parent)
            os.set_inheritable(fd_pre_cmd_child, True)
            argv = shlex.split(command)
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["LC_ALL"] = "en_US.UTF-8"
            os.execvpe(argv[0], argv, env)
            raise RuntimeError("execvpe failed")

        # Parent process: close write end of the pre-cmd pipe.
        os.close(fd_pre_cmd_child)
        self._pid = pid
        self._master_fd = fd
        self._p_out = os.fdopen(fd, "w+b", 0)
        self._p_out_pre_cmd = os.fdopen(fd_pre_cmd_parent, "w+b", 0)
        self.resize(rows, cols)
        return fd_pre_cmd_child

    def write(self, data: bytes) -> None:
        assert self._p_out is not None
        self._p_out.write(data)

    def resize(self, rows: int, cols: int) -> None:
        winsize = struct.pack("HH", rows, cols)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)

    def resume(self) -> None:
        with contextlib.suppress(ProcessLookupError, OSError):
            if self._pid > 0:
                os.kill(self._pid, signal.SIGCONT)

    def attach_readers(
        self,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        self._loop = loop
        p_out = self._p_out
        p_out_pre_cmd = self._p_out_pre_cmd
        assert p_out is not None
        assert p_out_pre_cmd is not None

        def on_output() -> None:
            try:
                read = p_out.read(65536).decode()
                recv_queue.put_nowait(["stdout", read])
            except UnicodeDecodeError as error:
                _logger.warning("decode error: %s", error)
            except Exception:  # noqa: BLE001
                loop.remove_reader(p_out)
                recv_queue.put_nowait(["disconnect", 1])

        def on_pre_cmd() -> None:
            try:
                recv_queue.put_nowait(["pre_cmd", p_out_pre_cmd.read(65536).decode()])
            except UnicodeDecodeError:
                pass
            except Exception:  # noqa: BLE001
                loop.remove_reader(p_out_pre_cmd)

        loop.add_reader(p_out, on_output)
        loop.add_reader(p_out_pre_cmd, on_pre_cmd)

    def detach_readers(self) -> None:
        if self._loop is not None:
            if self._p_out is not None:
                with contextlib.suppress(Exception):
                    self._loop.remove_reader(self._p_out)
            if self._p_out_pre_cmd is not None:
                with contextlib.suppress(Exception):
                    self._loop.remove_reader(self._p_out_pre_cmd)

    def teardown(self) -> None:
        with contextlib.suppress(OSError):
            if self._pid > 0:
                os.kill(self._pid, signal.SIGTERM)
        with contextlib.suppress(OSError):
            if self._pid > 0:
                os.waitpid(self._pid, os.WNOHANG)
        with contextlib.suppress(OSError):
            if self._p_out is not None:
                self._p_out.close()
        with contextlib.suppress(OSError):
            if self._p_out_pre_cmd is not None:
                self._p_out_pre_cmd.close()
        self._pid = -1
