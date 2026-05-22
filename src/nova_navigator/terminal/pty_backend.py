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
import re
import shlex
import signal
import struct
import termios
from abc import ABC, abstractmethod

_OSC_COMPLETE = re.compile(r"\033\](\d+);(.*?)(?:\007|\033\\)", re.DOTALL)
_OSC_PARTIAL = re.compile(r"\033\].*$", re.DOTALL)
_OSC_CWD = 7

_logger = logging.getLogger(__name__)


class PtyBackend(ABC):
    """Abstract base class for terminal process backends.

    A PtyBackend manages the lifecycle of a shell process and provides
    byte-level I/O.  It does not interpret the bytes — the Terminal widget
    feeds them to pyte for rendering.

    Lifecycle: ``open()`` → ``attach_readers()`` → (normal operation)
    → ``detach_readers()`` → ``teardown()``.
    """

    def __init__(self) -> None:
        self._osc_buf: str = ""

    @property
    def supports_precmd(self) -> bool:
        """True if this backend delivers precmd CWD notifications.

        Always True — all backends emit OSC 7 via the shell hook installed
        by ``ShellDriver.init_code()``.
        """
        return True

    def _process_chunk(
        self,
        data: bytes,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        """Decode *data*, strip OSC sequences, post stdout and pre_cmd messages.

        Prepends any buffered incomplete OSC sequence from the previous chunk.
        Complete sequences are dispatched to ``_on_osc``; the cleaned text is
        posted as ``["stdout", ...]``.  A trailing incomplete sequence is saved
        to ``_osc_buf`` for the next chunk.
        """
        text = self._osc_buf + data.decode("utf-8", errors="replace")
        self._osc_buf = ""

        def _dispatch(m: re.Match[str]) -> str:
            self._on_osc(int(m.group(1)), m.group(2), loop, recv_queue)
            return ""

        clean = _OSC_COMPLETE.sub(_dispatch, text)

        partial = _OSC_PARTIAL.search(clean)
        if partial:
            self._osc_buf = clean[partial.start() :]
            clean = clean[: partial.start()]

        if clean:
            loop.call_soon_threadsafe(recv_queue.put_nowait, ["stdout", clean])

    def _on_osc(
        self,
        code: int,
        data: str,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        """Handle a decoded OSC sequence.

        OSC 7 carries a ``file:///path`` URI and is posted as
        ``["pre_cmd", path]``.  All other codes are silently discarded.
        """
        if code == _OSC_CWD and data.startswith("file://"):
            remainder = data[len("file://") :]
            if remainder.startswith("/"):
                path = remainder
            else:
                slash = remainder.find("/")
                path = remainder[slash:] if slash != -1 else "/"
            loop.call_soon_threadsafe(recv_queue.put_nowait, ["pre_cmd", path])

    @abstractmethod
    def open(self, command: str, rows: int, cols: int) -> int | None:
        """Start the shell process.

        Args:
            command: The shell command to execute.
            rows: Initial terminal height.
            cols: Initial terminal width.

        Returns:
            Always ``None`` — CWD is now tracked in-band via OSC 7 sequences.
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


class LocalPtyBackend(PtyBackend):
    """PTY backend for local shell processes.

    Uses ``pty.fork()`` to create a pseudo-terminal.
    CWD is tracked via OSC 7 sequences scanned by the inherited
    ``_process_chunk`` method.
    """

    def __init__(self) -> None:
        super().__init__()
        self._pid: int = -1
        self._master_fd: int = -1
        self._p_out: io.FileIO | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def open(self, command: str, rows: int, cols: int) -> int | None:
        pid, fd = pty.fork()
        if pid == 0:
            # Child process
            argv = shlex.split(command)
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["LC_ALL"] = "en_US.UTF-8"
            os.execvpe(argv[0], argv, env)
            raise RuntimeError("execvpe failed")

        self._pid = pid
        self._master_fd = fd
        self._p_out = os.fdopen(fd, "w+b", 0)
        self.resize(rows, cols)
        return None

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
        assert p_out is not None

        def on_output() -> None:
            try:
                data = p_out.read(65536)
                self._process_chunk(data, loop, recv_queue)
            except UnicodeDecodeError as error:
                _logger.warning("decode error: %s", error)
            except Exception:  # noqa: BLE001
                loop.remove_reader(p_out)
                recv_queue.put_nowait(["disconnect", 1])

        loop.add_reader(p_out, on_output)

    def detach_readers(self) -> None:
        if self._loop is not None and self._p_out is not None:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self._p_out)

    def teardown(self) -> None:
        with contextlib.suppress(OSError):
            if self._pid > 0:
                os.kill(self._pid, signal.SIGTERM)
