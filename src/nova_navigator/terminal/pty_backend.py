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
import threading
import time
from abc import ABC, abstractmethod

_OSC_COMPLETE = re.compile(r"\033\](\d+);(.*?)(?:\007|\033\\)", re.DOTALL)
_OSC_PARTIAL = re.compile(r"\033\].*$", re.DOTALL)
_OSC_CWD = 7

_HANGUP_GRACE = 3.0  # seconds a shell may take to end after its terminal is closed, exit hooks included
_EXIT_POLL_INTERVAL = 0.02  # seconds between checks whether such a shell has ended

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
        Complete sequences are dispatched to ``_on_osc``; surrounding text is
        posted as ``["stdout", ...]`` in document order.  A trailing incomplete
        sequence is saved to ``_osc_buf`` for the next chunk.
        """
        text = self._osc_buf + data.decode("utf-8", errors="replace")
        self._osc_buf = ""
        pos = 0
        for m in _OSC_COMPLETE.finditer(text):
            if m.start() > pos:
                loop.call_soon_threadsafe(recv_queue.put_nowait, ["stdout", text[pos : m.start()]])
            self._on_osc(int(m.group(1)), m.group(2), loop, recv_queue)
            pos = m.end()
        remaining = text[pos:]
        partial = _OSC_PARTIAL.search(remaining)
        if partial:
            self._osc_buf = remaining[partial.start() :]
            remaining = remaining[: partial.start()]
        if remaining:
            loop.call_soon_threadsafe(recv_queue.put_nowait, ["stdout", remaining])

    def _on_osc(
        self,
        code: int,
        data: str,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        """Handle a decoded OSC sequence.

        OSC 7 carries a CWD URI and is posted as ``["pre_cmd", path, from_nn]``.
        The payload may be ``panel=;file:///path`` (NN format, ``from_nn=True``) or
        plain ``file:///path`` (third-party chpwd hook, ``from_nn=False``).
        ``from_nn`` lets callers ignore third-party hooks that would miscount
        in-flight navigations.
        All other OSC codes, including OSC 133 prompt markers emitted by
        third-party shell integrations, are silently discarded.
        """
        if code == _OSC_CWD:
            payload = data
            from_nn = data.startswith("panel=")
            if from_nn:
                semi = data.find(";", 6)
                if semi != -1:
                    payload = data[semi + 1 :]
            if payload.startswith("file://"):
                remainder = payload[len("file://") :]
                if remainder.startswith("/"):
                    path = remainder
                else:
                    slash = remainder.find("/")
                    path = remainder[slash:] if slash != -1 else "/"
                loop.call_soon_threadsafe(recv_queue.put_nowait, ["pre_cmd", path, from_nn])

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
        """Terminate the shell process and close all file objects.

        Safe to call before ``open()`` and more than once.
        """


def _reap_if_exited(pid: int) -> bool:
    """Reap the child *pid* if it has ended; return True when there is no child left to end."""
    try:
        return os.waitpid(pid, os.WNOHANG)[0] == pid
    except ChildProcessError:
        return True


def _kill_child(pid: int) -> None:
    """Kill and reap the child *pid*, which must not have been reaped yet.

    Only the child is signalled, not its jobs.
    """
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except OSError as error:
        _logger.warning("cannot kill shell %d: %s", pid, error)


def _end_child(pid: int) -> None:
    """Reap the child *pid*, killing it first when it does not end within ``_HANGUP_GRACE`` seconds.

    Nothing else reaps *pid*, so until it is reaped here it cannot belong to another process.
    """
    deadline = time.monotonic() + _HANGUP_GRACE
    while not _reap_if_exited(pid):
        if time.monotonic() >= deadline:
            _kill_child(pid)
            return
        time.sleep(_EXIT_POLL_INTERVAL)


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
            # Child process.  It must never return into the application: any failure
            # is reported on the terminal and ends the child with status 127.
            try:
                argv = shlex.split(command)
                env = os.environ.copy()
                env["TERM"] = "xterm-256color"
                env["LC_ALL"] = "en_US.UTF-8"
                # Remove virtualenv variables inherited from the parent process
                # (e.g. when launched via ``uv run``).  The child shell has no
                # ``deactivate`` function, so plugins like autoswitch_virtualenv
                # break when they see $VIRTUAL_ENV and try to call it.
                env.pop("VIRTUAL_ENV", None)
                env.pop("VIRTUAL_ENV_PROMPT", None)
                os.execvpe(argv[0], argv, env)
            except (OSError, ValueError, IndexError) as error:
                with contextlib.suppress(OSError):
                    os.write(2, f"nn: cannot start {command}: {error}\r\n".encode())
            finally:
                os._exit(127)

        # pty.fork() returns an inheritable master.  No later child may hold it open,
        # or closing it in teardown() would not hang up the terminal.
        os.set_inheritable(fd, False)
        self._pid = pid
        self._master_fd = fd
        self._p_out = os.fdopen(fd, "w+b", 0)
        self.resize(rows, cols)
        return None

    def write(self, data: bytes) -> None:
        """Write raw bytes to the shell's stdin; ignored after ``teardown()``."""
        assert self._p_out is not None
        if not self._p_out.closed:
            self._p_out.write(data)

    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal; ignored while no master is open."""
        if self._master_fd < 0:
            return
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
        """Hang up the terminal and signal the shell to end, without waiting for it.

        A shell that is still alive afterwards is watched by a thread, which reaps it and, after
        ``_HANGUP_GRACE`` seconds, kills it.  The thread is not a daemon, so the interpreter does
        not exit before the shell is gone.  When no thread can be started the shell is killed at once.
        """
        # The reader must go first: the loop cannot remove it once the file is closed.
        self.detach_readers()
        if self._pid > 0:
            with contextlib.suppress(OSError):
                os.kill(self._pid, signal.SIGTERM)
        # Interactive shells ignore SIGTERM.  Closing the master hangs up the terminal.
        if self._p_out is not None:
            self._p_out.close()
            self._master_fd = -1
        if self._pid <= 0:
            return
        # The child is reaped here or by the thread.  Once reaped the pid may be reused by another
        # process, so it is forgotten and never signalled again.
        pid, self._pid = self._pid, -1
        if _reap_if_exited(pid):
            return
        try:
            threading.Thread(target=_end_child, args=(pid,), name=f"end-shell-{pid}").start()
        except RuntimeError as error:
            _logger.warning("cannot watch shell %d, killing it: %s", pid, error)
            _kill_child(pid)
