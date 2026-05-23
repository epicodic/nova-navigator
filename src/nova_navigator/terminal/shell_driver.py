"""Shell driver abstraction for terminal hook installation and argument quoting.

This module isolates all shell-language knowledge from the Terminal widget.
Each concrete ShellDriver knows how to:
- Install a precmd hook that emits an OSC 7 CWD sequence and optionally stops the shell.
- Quote arbitrary strings for safe shell interpolation.

The Terminal widget delegates to a ShellDriver for all shell-specific operations,
allowing transparent support for zsh, bash, and POSIX sh.

Related modules:
- ``pty_backend.py`` — OS-level PTY transport (start/stop process, I/O).
- ``terminal.py`` — Textual widget (rendering, draining, event handling).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import PurePath

_logger = logging.getLogger(__name__)

_SAFE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/._-")

_LINE_CONTINUATION_LIMIT = 250


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


def _posix_octal_escape(arg: str) -> str:
    r"""Escape *arg* as a sequence of ``\0ooo`` octal codes for ``printf '%b'``.

    This is the POSIX sh fallback quoting used by Midnight Commander when
    ANSI-C ``$'...'`` is not available.
    """
    return "".join(f"\\0{ord(char):03o}" for char in arg)


class ShellDriver(ABC):
    """Abstract base class for shell-specific terminal integration."""

    def __init__(self, *, stop_resume: bool, prompt_ready: bool) -> None:
        self._stop_resume = stop_resume
        self._prompt_ready = prompt_ready

    @property
    def supports_stop_resume(self) -> bool:
        """True if init_code() includes ``kill -STOP $$``."""
        return self._stop_resume

    @property
    def supports_prompt_ready(self) -> bool:
        """True if init_code() installs an OSC 133;B prompt-end hook."""
        return self._prompt_ready

    def _hook_body(self) -> str:
        """Return the core of the precmd hook function body.

        Emits an OSC 7 sequence reporting the current directory.
        The payload format is ``panel=<id>;file:///path`` where ``<id>`` is the
        value of ``$_NN_PANEL`` (empty string when unset).
        Appends ``kill -STOP $$`` when stop/resume is enabled.
        """
        stop_part = "; kill -STOP $$" if self._stop_resume else ""
        return 'printf \'\\033]7;panel=%s;file://%s\\007\' "${_NN_PANEL:-}" "$(pwd)"' + stop_part

    @abstractmethod
    def init_code(self) -> str:
        """Return shell code to inject at startup.

        The code must set up a precmd hook that emits an OSC 7 CWD sequence.

        Returns:
            A string of shell code ending with a newline.
        """

    @abstractmethod
    def quote(self, arg: str) -> str:
        """Return a shell-safe quoted form of *arg*."""

    def cd_command(self, path: str) -> str:
        """Return a complete shell command that changes directory to *path*."""
        return f"cd {self.quote(path)}"


class ZshDriver(ShellDriver):
    """Shell driver for zsh."""

    def __init__(self, *, stop_resume: bool = True) -> None:
        super().__init__(stop_resume=stop_resume, prompt_ready=True)

    def init_code(self) -> str:
        zle_hook = " _nn_zle_init() { printf '\\033]133;B\\007' >/dev/tty }; zle -N zle-line-init _nn_zle_init"
        return (
            f" setopt HIST_IGNORE_SPACE; _nn_precmd() {{ {self._hook_body()} }};"
            f" precmd_functions+=(_nn_precmd);{zle_hook}\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)


class BashDriver(ShellDriver):
    """Shell driver for bash."""

    def __init__(self, *, stop_resume: bool = True) -> None:
        super().__init__(stop_resume=stop_resume, prompt_ready=True)

    def init_code(self) -> str:
        return (
            ' HISTCONTROL="${HISTCONTROL:+${HISTCONTROL}:}ignorespace";'
            f" _nn_precmd() {{ {self._hook_body()}; }};"
            " PROMPT_COMMAND=${PROMPT_COMMAND:+${PROMPT_COMMAND}$'\\n'}_nn_precmd;"
            " PS1=\"${PS1}\"$'\\[\\033]133;B\\007\\]'\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)


class FallbackDriver(ShellDriver):
    """Shell driver for generic POSIX sh.

    No SIGSTOP/SIGCONT synchronisation.  Uses PS1 substitution for the hook;
    printf must redirect to /dev/tty to avoid the OSC 7 sequence polluting
    the prompt text.
    """

    def __init__(self, *, stop_resume: bool = False) -> None:
        super().__init__(stop_resume=False, prompt_ready=False)  # FallbackDriver never supports either

    def init_code(self) -> str:
        return f" _nn_precmd() {{ {self._hook_body()} >/dev/tty; }}; PS1='$(_nn_precmd)'\"$PS1\"\n"

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def cd_command(self, path: str) -> str:
        escaped = _posix_octal_escape(path)
        return f"_nn_newdir_=`printf '%b_' '{escaped}'`; cd \"${{_nn_newdir_%_}}\""


def detect_driver(command: str, *, stop_resume: bool = True) -> ShellDriver:
    """Return the appropriate ShellDriver for *command*.

    Args:
        command: Shell command path (e.g. ``"/usr/bin/zsh"``).
        stop_resume: Whether to enable SIGSTOP/SIGCONT synchronisation.
            Pass ``False`` for remote shells (SSH) that don't support it.

    Returns:
        A ``ZshDriver``, ``BashDriver``, or ``FallbackDriver`` instance.
        ``FallbackDriver`` always has ``stop_resume=False`` regardless of the kwarg.
    """
    name = PurePath(command.split()[0]).name
    if name == "zsh":
        return ZshDriver(stop_resume=stop_resume)
    if name == "bash":
        return BashDriver(stop_resume=stop_resume)
    return FallbackDriver()
