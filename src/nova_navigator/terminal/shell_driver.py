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
import shlex
from abc import ABC, abstractmethod
from pathlib import PurePath

_logger = logging.getLogger(__name__)

_SAFE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/._-")

_LINE_CONTINUATION_LIMIT = 250

# bash 5.2 can loop forever when the PTY has one row and its width changes, or shrinks to one row,
# while a typed line is wider than the terminal and the prompt has invisible characters.
_MIN_PTY_ROWS = 2

# Private, unassigned CSI function-key sequences used to request an atomic
# line-editor stash/restore from drivers with supports_editor_protocol.
STASH_SEQUENCE = b"\x1b[98~"
RESTORE_SEQUENCE = b"\x1b[97~"

# Private, unassigned CSI sequences that the init code of ZshDriver and BashDriver
# binds in the emacs, vi insert and vi command keymaps (zsh: also in the main one).
# The terminal writes only these (and a space) to edit the line, so neither the
# editing mode nor the user's own key bindings change what a hidden write does.
#
# Switch to a mode that takes typed text; does nothing outside vi command mode.
_INSERT_MODE_SEQUENCE = b"\x1b[95~"
# zsh: one widget each stashes and restores the whole buffer.
_ZSH_STASH_SEQUENCE = b"\x1b[96~"
_ZSH_RESTORE_SEQUENCE = b"\x1b[94~"
# bash: readline functions, bound in the emacs and vi insert keymaps.
# Without line editing (set +o emacs) all of them stay plain input.  The one written alone, before a
# write that stashes nothing, therefore ends in Ctrl+U: the line discipline then erases it, so that the
# cd on an empty line works.  readline takes it as one key and does what _INSERT_MODE_SEQUENCE does.
# The stash must not send Ctrl+U: a pending readline search or vi command would take it as editing.
_BASH_PREPARE_SEQUENCE = b"\x1b[95\x15"
_BASH_END_OF_LINE_SEQUENCE = b"\x1b[91~"  # end-of-line
_BASH_KILL_SEQUENCE = b"\x1b[92~"  # unix-line-discard
_BASH_YANK_SEQUENCE = b"\x1b[93~"  # yank
_BASH_BACKSPACE_SEQUENCE = b"\x1b[94~"  # backward-delete-char


def _ansi_c_quote(arg: str) -> str:
    r"""Quote *arg* using ANSI-C ``$'...'`` syntax with octal escapes.

    Every byte outside ``[a-zA-Z0-9/._-]`` is escaped as ``\\ooo`` (3-digit octal).
    *arg* is encoded to UTF-8 first and escaped byte by byte, not by Unicode
    codepoint: a codepoint above ``0x7f`` spans multiple UTF-8 bytes, so escaping
    by codepoint would emit the wrong (single, invalid) byte for it.
    Every 250 bytes the quote is split as ``'\<newline>$'``: the quote is closed,
    a backslash and newline are written outside it, and ``$'`` reopens it.  The
    shell reads the result as one word, and each physical line stays within the
    kernel cooked-mode buffer limit on some platforms.  A backslash and newline
    inside ``$'...'`` would not be a line continuation, so the split must sit
    outside the quote.
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
            parts.append("'\\\n$'")
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
        """True if the shell has an interactive line editor the terminal can clear and refill.

        Drivers with line editing take part in hidden directory navigation:
        typed text is stashed before the internal ``cd`` and restored after it.
        """
        return self._line_editing

    @property
    def min_rows(self) -> int:
        """The smallest height the PTY and the screen model get, however small the widget is.

        A widget with fewer rows shows only the cursor's row of that screen.
        """
        return _MIN_PTY_ROWS

    @property
    def supports_editor_protocol(self) -> bool:
        """True if hidden navigation can stash/restore the line editor atomically.

        Only the virtual VFS shell driver supports this: it owns the line
        editor directly, so it can swap its state in one step instead of
        sending the key sequences real shells have to bind first.
        """
        return False

    def prepare_line_sequence(self) -> bytes:
        """Return the input bytes that make the line editor take typed text.

        Written before a hidden write that does not start with
        ``kill_line_sequence``: in vi command mode its characters would act as
        editor commands.  Empty for shells without such bindings.
        """
        return b""

    def kill_line_sequence(self) -> bytes:
        """Return the input bytes that clear the line editor's whole buffer for hidden navigation.

        The buffer may hold several lines (a command recalled from history).
        The bytes work in every editing mode and leave a mode that takes typed
        text.  Only called for drivers with ``supports_line_editing`` and
        without ``supports_editor_protocol``.
        """
        return b""

    def yank_sequence(self) -> bytes:
        """Return the input bytes that restore text previously cleared by ``kill_line_sequence``.

        The cursor ends after the restored text.  Only called for drivers with
        ``supports_line_editing`` and without ``supports_editor_protocol``.
        """
        return b""

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

    @property
    def min_rows(self) -> int:
        """No minimum: zsh handles a one-row terminal itself."""
        return 0

    def init_code(self) -> str:
        """Install the hook and the widgets for hidden writes.

        The hook binds the keys before every prompt, so they survive ``bindkey -d``:
        an unbound stash key would leave the typed line to be run with the ``cd``.
        It also binds them in ``main``, which can be a keymap of the user's own.
        The stash widget keeps the buffer in a variable, not in zsh's kill
        buffer: ``kill-buffer`` appends to the user's previous kill, so the
        restore could add text, and the user's kill ring stays as it was.
        """
        return (
            " setopt HIST_IGNORE_SPACE; "
            "_nn_insert() { REGION_ACTIVE=0; [[ $KEYMAP != vicmd ]] || zle -K main }; "
            "_nn_stash() { _nn_insert; typeset -g _nn_line=$BUFFER; BUFFER= }; "
            "_nn_restore() { _nn_insert; LBUFFER+=$_nn_line }; "
            "zle -N _nn_insert; zle -N _nn_stash; zle -N _nn_restore; "
            "_nn_bind() { local map; for map in main emacs viins vicmd; do bindkey -M $map "
            "$'\\e[95~' _nn_insert $'\\e[96~' _nn_stash $'\\e[94~' _nn_restore; done }; "
            f"_nn_precmd() {{ {self._hook_body()}; _nn_bind }}; precmd_functions+=(_nn_precmd)\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def prepare_line_sequence(self) -> bytes:
        return _INSERT_MODE_SEQUENCE

    def kill_line_sequence(self) -> bytes:
        """Send the insert mode key and then the private stash key.

        A command waiting for a key (Ctrl+V, a lone Escape) takes the escape
        byte of the first key, so the stash key still arrives whole.  Its widget
        sets the buffer itself, so no key that zsh-autosuggestions treats as
        accepting a suggestion (such as Ctrl+E) is involved.
        """
        return _INSERT_MODE_SEQUENCE + _ZSH_STASH_SEQUENCE

    def yank_sequence(self) -> bytes:
        return _ZSH_RESTORE_SEQUENCE


class BashDriver(ShellDriver):
    """Shell driver for bash."""

    def __init__(self) -> None:
        super().__init__(line_editing=True)

    def init_code(self) -> str:
        """Install the hook and the keys for hidden writes, and turn readline's horizontal scrolling off.

        With horizontal scrolling, bash 5.2 can loop forever when the width changes, whatever the
        height.  A bash without line editing only warns about each ``bind``, which is discarded.
        """
        # An empty macro does nothing: outside vi command mode there is no mode to change.
        return (
            f" bind 'set horizontal-scroll-mode off' 2>/dev/null; "
            "for _nn_map in emacs vi-insert; do bind -m $_nn_map "
            """'"\\e[95~": ""' '"\\e[95\\C-u": ""' '"\\e[91~": end-of-line' '"\\e[92~": unix-line-discard' """
            """'"\\e[93~": yank' '"\\e[94~": backward-delete-char' 2>/dev/null; done; unset _nn_map; """
            """bind -m vi-command '"\\e[95~": vi-insertion-mode' '"\\e[95\\C-u": vi-insertion-mode' 2>/dev/null; """
            f"HISTCONTROL=\"${{HISTCONTROL:+${{HISTCONTROL}}:}}ignorespace\"; _nn_precmd() {{ {self._hook_body()}; }}; PROMPT_COMMAND=${{PROMPT_COMMAND:+${{PROMPT_COMMAND}}$'\\n'}}_nn_precmd\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def prepare_line_sequence(self) -> bytes:
        return _BASH_PREPARE_SEQUENCE

    def kill_line_sequence(self) -> bytes:
        """Move to the end of the buffer, type a space, and kill back to the start.

        Readline's kill only reaches back from the cursor, hence the move.  The
        space makes sure the kill is never empty: an empty kill leaves the kill
        ring untouched, so a later restore would yank back stale text.  Being
        killed last, it is also restored last, and ``yank_sequence`` deletes it.
        Without line editing (``set +o emacs``) this does not remove the typed line.
        """
        return _INSERT_MODE_SEQUENCE + _BASH_END_OF_LINE_SEQUENCE + b" " + _BASH_KILL_SEQUENCE

    def yank_sequence(self) -> bytes:
        """Yank, then delete the space ``kill_line_sequence`` typed."""
        return _INSERT_MODE_SEQUENCE + _BASH_YANK_SEQUENCE + _BASH_BACKSPACE_SEQUENCE


class FallbackDriver(ShellDriver):
    """Shell driver for generic POSIX sh.

    No SIGSTOP/SIGCONT synchronisation.  Uses PS1 substitution for the hook;
    printf must redirect to /dev/tty to avoid the OSC 7 sequence polluting
    the prompt text.
    """

    def __init__(self) -> None:
        super().__init__(line_editing=False)

    def init_code(self) -> str:
        return f" stty erase '^H'; _nn_precmd() {{ {self._hook_body()} >/dev/tty; }}; PS1='$(_nn_precmd)'\"$PS1\"\n"

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
    name = PurePath(shlex.split(command)[0]).name
    if name == "zsh":
        return ZshDriver()
    if name == "bash":
        return BashDriver()
    return FallbackDriver()
