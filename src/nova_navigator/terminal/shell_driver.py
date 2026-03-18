"""Shell driver abstraction for terminal hook installation and argument quoting.

This module isolates all shell-language knowledge from the Terminal widget.
Each concrete ShellDriver knows how to:
- Install a precmd hook that writes CWD to a pipe and optionally stops the shell.
- Quote arbitrary strings for safe shell interpolation.
- Parse the precmd pipe output back into a pid and path.

The Terminal widget delegates to a ShellDriver for all shell-specific operations,
allowing transparent support for zsh, bash, and POSIX sh.

Related modules:
- ``pty_backend.py`` — OS-level PTY transport (start/stop process, I/O).
- ``terminal.py`` — Textual widget (rendering, draining, event handling).
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from pathlib import PurePath

_logger = logging.getLogger(__name__)

_SAFE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/._-")

_LINE_CONTINUATION_LIMIT = 250

_RE_PID_PATH = re.compile(r"^\s*(\d+):(.+)$")


def _ansi_c_quote(arg: str) -> str:
    r"""Quote *arg* using ANSI-C ``$'...'`` syntax with octal escapes.

    Every byte outside ``[a-zA-Z0-9/._-]`` is escaped as ``\\ooo`` (3-digit octal).
    Line continuations (``\\\\\\n``) are inserted every 250 bytes to stay within
    the kernel cooked-mode buffer limit on some platforms.
    """
    parts: list[str] = []
    line_len = 0
    for char in arg:
        if char in _SAFE_CHARS:
            parts.append(char)
            line_len += 1
        else:
            escaped = f"\\{ord(char):03o}"
            parts.append(escaped)
            line_len += len(escaped)
        if line_len >= _LINE_CONTINUATION_LIMIT:
            parts.append("\\\n")
            line_len = 0
    return "$'" + "".join(parts) + "'"


def _parse_pid_colon_path(raw: str) -> tuple[int | None, PurePath]:
    """Parse a ``PID:/path`` precmd message.

    Returns ``(pid, cwd)`` on success, or ``(None, PurePath("/"))`` if the
    payload is malformed.
    """
    cleaned = raw.strip()
    match = _RE_PID_PATH.match(cleaned)
    if match:
        return int(match.group(1)), PurePath(match.group(2))
    _logger.warning("Malformed precmd payload: %r", raw)
    return None, PurePath("/")


def _posix_octal_escape(arg: str) -> str:
    r"""Escape *arg* as a sequence of ``\0ooo`` octal codes for ``printf '%b'``.

    This is the POSIX sh fallback quoting used by Midnight Commander when
    ANSI-C ``$'...'`` is not available.
    """
    return "".join(f"\\0{ord(char):03o}" for char in arg)


class ShellDriver(ABC):
    """Abstract base class for shell-specific terminal integration.

    A ShellDriver knows how to:
    - Generate init code that installs a precmd hook in the shell.
    - Quote arguments safely for that shell's syntax.
    - Build a cd command.
    - Parse precmd pipe output.

    Concrete subclasses exist for zsh, bash, and a POSIX sh fallback.
    The Terminal widget delegates to a ShellDriver for all operations
    that depend on the shell language.
    """

    @abstractmethod
    def init_code(self, precmd_fd: int | None) -> str:
        """Return shell code to inject at startup.

        The code must set up a precmd hook that writes the shell's PID and
        current working directory to file descriptor *precmd_fd*.

        Args:
            precmd_fd: The fd number of the write end of the precmd pipe,
                or None if the backend has no precmd pipe.

        Returns:
            A string of shell code ending with a newline, or an empty string
            if no hook can be installed.
        """

    @abstractmethod
    def quote(self, arg: str) -> str:
        """Return a shell-safe quoted form of *arg*.

        For bash/zsh this uses ANSI-C ``$'...'`` quoting with octal escapes.
        """

    def cd_command(self, path: str) -> str:
        """Return a complete shell command that changes directory to *path*.

        The default implementation prepends ``cd`` to the quoted path.
        ``FallbackDriver`` overrides this with a different quoting strategy.
        """
        return f"cd {self.quote(path)}"

    @property
    @abstractmethod
    def supports_stop_resume(self) -> bool:
        """True if ``init_code()`` includes ``kill -STOP $$``.

        When True, the Terminal widget expects the shell to stop after each
        precmd and will send SIGCONT via the backend to resume it.
        """

    @abstractmethod
    def parse_precmd_payload(self, raw: str) -> tuple[int | None, PurePath]:
        """Parse a raw precmd pipe message.

        Args:
            raw: The raw string read from the precmd pipe.

        Returns:
            A tuple of (shell_pid, cwd).  shell_pid is None when
            stop/resume is not used or the payload is malformed.
        """


class ZshDriver(ShellDriver):
    """Shell driver for zsh.

    Installs a precmd hook via ``precmd_functions`` that writes ``PID:CWD``
    to the precmd pipe and then sends ``kill -STOP $$`` to freeze the shell
    until the Terminal widget sends SIGCONT.
    """

    @property
    def supports_stop_resume(self) -> bool:
        return True

    def init_code(self, precmd_fd: int | None) -> str:
        if precmd_fd is None:
            return ""
        return (
            f" setopt HIST_IGNORE_SPACE;"
            f" _nn_precmd() {{ printf '%d:%s\\n' $$ $(pwd) >&{precmd_fd};"
            f" kill -STOP $$ }};"
            f" precmd_functions+=(_nn_precmd)\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def parse_precmd_payload(self, raw: str) -> tuple[int | None, PurePath]:
        return _parse_pid_colon_path(raw)


class BashDriver(ShellDriver):
    """Shell driver for bash.

    Installs a precmd hook via ``PROMPT_COMMAND`` that writes ``PID:CWD``
    to the precmd pipe and then sends ``kill -STOP $$`` to freeze the shell.
    Uses the same ANSI-C quoting and precmd format as ``ZshDriver``.
    """

    @property
    def supports_stop_resume(self) -> bool:
        return True

    def init_code(self, precmd_fd: int | None) -> str:
        if precmd_fd is None:
            return ""
        return (
            f' HISTCONTROL="${{HISTCONTROL:+${{HISTCONTROL}}:}}ignorespace";'
            f" _nn_precmd() {{ printf '%d:%s\\n' $$ $(pwd) >&{precmd_fd};"
            f" kill -STOP $$; }};"
            " PROMPT_COMMAND=${PROMPT_COMMAND:+${PROMPT_COMMAND}$'\\n'}_nn_precmd\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def parse_precmd_payload(self, raw: str) -> tuple[int | None, PurePath]:
        return _parse_pid_colon_path(raw)


class FallbackDriver(ShellDriver):
    """Shell driver for generic POSIX sh.

    No SIGSTOP/SIGCONT synchronisation.  The cd command is visible on screen
    (accepted degraded behaviour).  Typed input is lost on navigation.

    Uses the Midnight Commander ``printf '%b_'`` trick for cd commands, since
    POSIX sh does not support ANSI-C ``$'...'`` quoting.
    """

    @property
    def supports_stop_resume(self) -> bool:
        return False

    def init_code(self, precmd_fd: int | None) -> str:
        if precmd_fd is None:
            return ""
        return f" _nn_precmd() {{ pwd >&{precmd_fd}; }}; PS1='$(_nn_precmd)'\"$PS1\"\n"

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def cd_command(self, path: str) -> str:
        escaped = _posix_octal_escape(path)
        return f"_nn_newdir_=`printf '%b_' '{escaped}'`; cd \"${{_nn_newdir_%_}}\""

    def parse_precmd_payload(self, raw: str) -> tuple[int | None, PurePath]:
        cleaned = raw.strip()
        if not cleaned:
            _logger.warning("Empty precmd payload")
            return None, PurePath("/")
        return None, PurePath(cleaned)


def detect_driver(command: str) -> ShellDriver:
    """Return the appropriate ShellDriver for *command*.

    Inspects the basename of the first word in *command* to determine the shell.
    Falls back to ``FallbackDriver`` for unrecognised shells.
    """
    name = PurePath(command.split()[0]).name
    if name == "zsh":
        return ZshDriver()
    if name == "bash":
        return BashDriver()
    return FallbackDriver()
