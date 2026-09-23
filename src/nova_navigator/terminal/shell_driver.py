"""Shell driver abstraction for terminal hook installation and argument quoting.

This module isolates all shell-language knowledge from the Terminal widget.
Each concrete ShellDriver knows how to:
- Install a precmd hook that emits an OSC 7 CWD sequence.
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

# Private, unassigned CSI function-key sequences used to request an atomic
# line-editor stash/restore from drivers with supports_editor_protocol.
STASH_SEQUENCE = b"\x1b[98~"
RESTORE_SEQUENCE = b"\x1b[97~"

# Private, unassigned CSI sequence bound by ZshDriver.init_code() to a widget
# that calls the shell's raw, unwrapped end-of-line motion (see
# ZshDriver.kill_line_sequence()).
_ZSH_RAW_END_OF_LINE_SEQUENCE = b"\x1b[96~"

# Real-shell (emacs-mode) line editor control characters for hidden navigation.
_END_OF_LINE = "\x05"  # Ctrl+E
_KILL_LINE = "\x15"  # Ctrl+U
_YANK = "\x19"  # Ctrl+Y
_BACKSPACE = "\x08"  # Backward-delete-char


def _ansi_c_quote(arg: str) -> str:
    r"""Quote *arg* using ANSI-C ``$'...'`` syntax with octal escapes.

    Every byte outside ``[a-zA-Z0-9/._-]`` is escaped as ``\\ooo`` (3-digit octal).
    *arg* is encoded to UTF-8 first and escaped byte by byte, not by Unicode
    codepoint: a codepoint above ``0x7f`` spans multiple UTF-8 bytes, so escaping
    by codepoint would emit the wrong (single, invalid) byte for it.
    Line continuations (``\\\\\\n``) are inserted every 250 bytes to stay within
    the kernel cooked-mode buffer limit on some platforms.
    """
    parts: list[str] = []
    line_len = 0
    for byte in arg.encode("utf-8"):
        char = chr(byte)
        if char in _SAFE_CHARS:
            parts.append(char)
            line_len += 1
        else:
            escaped = f"\\{byte:03o}"
            parts.append(escaped)
            line_len += len(escaped)
        if line_len >= _LINE_CONTINUATION_LIMIT:
            parts.append("\\\n")
            line_len = 0
    return "$'" + "".join(parts) + "'"


def _posix_octal_escape(arg: str) -> str:
    r"""Escape *arg* as a sequence of ``\0ooo`` octal codes for ``printf '%b'``.

    This is the POSIX sh fallback quoting used by Midnight Commander when
    ANSI-C ``$'...'`` is not available.  *arg* is encoded to UTF-8 first and
    escaped byte by byte, not by Unicode codepoint, for the same reason as
    ``_ansi_c_quote``: a codepoint above ``0x7f`` spans multiple UTF-8 bytes.
    """
    return "".join(f"\\0{byte:03o}" for byte in arg.encode("utf-8"))


class ShellDriver(ABC):
    """Abstract base class for shell-specific terminal integration."""

    def __init__(self, *, line_editing: bool) -> None:
        self._line_editing = line_editing

    @property
    def supports_line_editing(self) -> bool:
        """True if the shell has an interactive line editor with emacs-style kill and yank.

        Drivers with line editing take part in hidden directory navigation:
        typed text is killed before the internal ``cd`` and yanked back after it.
        """
        return self._line_editing

    @property
    def supports_editor_protocol(self) -> bool:
        """True if hidden navigation can stash/restore the line editor atomically.

        Only the virtual VFS shell driver supports this: it owns the line
        editor directly, so it can swap its state in one step instead of
        injecting Ctrl+E/Ctrl+U/Ctrl+Y bytes as real shells require.
        """
        return False

    def kill_line_sequence(self) -> bytes:
        """Return the input bytes that clear the line editor's buffer for hidden navigation.

        Moves to the end of the line first (a backward-only kill, as in bash,
        would otherwise leave trailing text behind), then types a literal space
        before killing so the kill is never empty: an empty kill leaves the line
        editor's kill ring/cutbuffer untouched, so a later spurious kill (e.g.
        one issued on an already-empty line) would still yank back stale,
        unrelated text via :meth:`yank_sequence`.  Moving to the end first also
        guarantees the marker space is always the *last* character killed, so
        :meth:`yank_sequence` can always remove exactly it and nothing else.
        Only called for drivers with ``supports_line_editing``.
        """
        return (_END_OF_LINE + " " + _KILL_LINE).encode()

    def yank_sequence(self) -> bytes:
        """Return the input bytes that restore text previously cleared by ``kill_line_sequence``.

        A trailing backspace removes the marker space ``kill_line_sequence``
        typed before the kill.  Because that space is always the last character
        killed, it is also always the last character yank restores, so the
        backspace removes exactly it -- never real typed text -- leaving the
        cursor at the true end of the restored line.  Only called for drivers
        with ``supports_line_editing``.
        """
        return (_YANK + _BACKSPACE).encode()

    def _hook_body(self) -> str:
        """Return the core of the precmd hook function body.

        Emits an OSC 7 sequence reporting the current directory.
        The payload format is ``panel=;file:///path``.  The ``panel=`` prefix
        marks the sequence as originating from Nova Navigator's own hook
        (as opposed to third-party chpwd hooks that emit plain ``file://``).
        """
        return "printf '\\033]7;panel=;file://%s\\007' \"$(pwd)\""

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

    def __init__(self) -> None:
        super().__init__(line_editing=True)

    def init_code(self) -> str:
        return (
            f" setopt HIST_IGNORE_SPACE; _nn_precmd() {{ {self._hook_body()} }}; precmd_functions+=(_nn_precmd); "
            "_nn_raw_eol() { zle .end-of-line }; zle -N _nn_raw_eol; bindkey $'\\e[96~' _nn_raw_eol\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def kill_line_sequence(self) -> bytes:
        """Kill after moving to the end via a private, unwrapped end-of-line widget.

        Real Ctrl+E (the ``end-of-line`` widget) is one of zsh-autosuggestions'
        default accept-widgets: sending it while a suggestion is showing
        silently accepts that suggestion into the real buffer.  ``init_code()``
        binds :data:`_ZSH_RAW_END_OF_LINE_SEQUENCE` to a widget that calls
        ``zle .end-of-line`` -- the dot-prefixed form always refers to zsh's
        original, unwrapped builtin, regardless of what any plugin has layered
        onto the public ``end-of-line`` name -- so this reaches the true end of
        the line without ever risking acceptance.  Zsh's Ctrl+U (kill-whole-line)
        then removes the whole buffer regardless of cursor position, same as the
        base implementation.
        """
        return _ZSH_RAW_END_OF_LINE_SEQUENCE + (" " + _KILL_LINE).encode()

    def yank_sequence(self) -> bytes:
        """Yank, then backspace off the marker space typed by ``kill_line_sequence``.

        Same rationale as the base implementation, plus: a trailing Ctrl+E here
        would risk accepting a new autosuggestion formed from the just-restored
        text, so backspace (not Ctrl+E) is used to reach the true end, same as
        ``kill_line_sequence`` avoids Ctrl+E to position before the kill.
        """
        return (_YANK + _BACKSPACE).encode()


class BashDriver(ShellDriver):
    """Shell driver for bash."""

    def __init__(self) -> None:
        super().__init__(line_editing=True)

    def init_code(self) -> str:
        return f" HISTCONTROL=\"${{HISTCONTROL:+${{HISTCONTROL}}:}}ignorespace\"; _nn_precmd() {{ {self._hook_body()}; }}; PROMPT_COMMAND=${{PROMPT_COMMAND:+${{PROMPT_COMMAND}}$'\\n'}}_nn_precmd\n"

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)


class FallbackDriver(ShellDriver):
    """Shell driver for generic POSIX sh.

    No SIGSTOP/SIGCONT synchronisation.  Uses PS1 substitution for the hook;
    printf must redirect to /dev/tty to avoid the OSC 7 sequence polluting
    the prompt text.
    """

    def __init__(self) -> None:
        super().__init__(line_editing=False)

    def init_code(self) -> str:
        return f" _nn_precmd() {{ {self._hook_body()} >/dev/tty; }}; PS1='$(_nn_precmd)'\"$PS1\"\n"

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def cd_command(self, path: str) -> str:
        escaped = _posix_octal_escape(path)
        return f"_nn_newdir_=`printf '%b_' '{escaped}'`; cd \"${{_nn_newdir_%_}}\""


def detect_driver(command: str) -> ShellDriver:
    """Return the appropriate ShellDriver for *command*.

    Args:
        command: Shell command path (e.g. ``"/usr/bin/zsh"``).

    Returns:
        A ``ZshDriver``, ``BashDriver``, or ``FallbackDriver`` instance.
    """
    name = PurePath(command.split()[0]).name
    if name == "zsh":
        return ZshDriver()
    if name == "bash":
        return BashDriver()
    return FallbackDriver()
