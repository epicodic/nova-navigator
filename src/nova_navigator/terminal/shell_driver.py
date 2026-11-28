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

# The oldest bash major version whose line the terminal sets aside.  An older bash may not run a
# function bound with bind -x to a multi-byte key, and a stash that does nothing would let a hidden cd
# run the typed line.  Such a bash is treated like one without line editing.
_BASH_MIN_EDITING_VERSION = 5

# bash 5.2 can loop forever when the PTY has one row and its width changes, or shrinks to one row,
# while a typed line is wider than the terminal and the prompt has invisible characters.
_MIN_PTY_ROWS = 2

# Private, unassigned CSI function-key sequences used to request an atomic
# line-editor stash/restore from drivers with supports_editor_protocol.
STASH_SEQUENCE = b"\x1b[98~"
RESTORE_SEQUENCE = b"\x1b[97~"

# Private, unassigned CSI sequences that the init code of ZshDriver and BashDriver binds
# (zsh: in every keymap that can be bound; bash: in the emacs, vi insert and vi command keymaps).
# The terminal writes only these to edit the line, so neither the editing mode nor the
# user's own key bindings change what a hidden write does.
#
# Switch to a mode that takes typed text; does nothing outside vi command mode.
_INSERT_MODE_SEQUENCE = b"\x1b[95~"
# One widget (zsh) or shell function (bash) each sets the whole line aside and puts it back.
_SET_ASIDE_SEQUENCE = b"\x1b[96~"
_PUT_BACK_SEQUENCE = b"\x1b[94~"
# bash only: dismisses readline's completion question and pager, which read single keys.  They ignore
# the bytes before the final Ctrl+G, which ends them without touching the line.  At the line editor
# the whole sequence is bound to nothing, so the user's own Ctrl+G is not involved.
_DISMISS_SEQUENCE = b"\x1b[93\x07"
# zsh only: bound to vi-history-search-backward and send-break, to cancel a search or named command
# that is waiting for more keys.
_ZSH_SEARCH_SEQUENCE = b"\x1b[93~"
_ZSH_BREAK_SEQUENCE = b"\x1b[92~"


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

    def set_aside_sequence(self) -> bytes:
        """Return the input bytes that make the shell set aside everything its line editor holds.

        The buffer may hold several lines (a command recalled from history), and
        the shell may hold more lines that it would put at a later prompt.  The
        bytes are written before every hidden line, also onto an empty line, and
        saved text accumulates until ``put_back_sequence``.  They work in every
        editing mode and leave a mode that takes typed text.  Only called for
        drivers with ``supports_line_editing`` and without
        ``supports_editor_protocol``.
        """
        return b""

    def put_back_sequence(self) -> bytes:
        """Return the input bytes that restore everything set aside by ``set_aside_sequence``.

        The cursor ends after the restored text.  Only called for drivers with
        ``supports_line_editing`` and without ``supports_editor_protocol``.
        """
        return b""

    def _hook_body(self, panel: str = "") -> str:
        """Return the core of the precmd hook function body.

        Emits an OSC 7 sequence reporting the current directory.
        The payload format is ``panel=;file:///path``.  The ``panel=`` prefix
        marks the sequence as originating from Nova Navigator's own hook
        (as opposed to third-party chpwd hooks that emit plain ``file://``).
        *panel* is ``noedit`` when the shell cannot set its line aside.
        """
        return f"printf '\\033]7;panel={panel};file://%s\\007' \"$(pwd)\""

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
        It binds them in every keymap that can be bound, including ``main`` and
        keymaps of the user's own, and the widgets leave any keymap but ``main``,
        ``emacs`` and ``viins`` for ``main``.
        The stash widget keeps the buffer in a variable, not in zsh's kill
        buffer: ``kill-buffer`` appends to the user's previous kill, so the
        restore could add text, and the user's kill ring stays as it was.
        It also empties zsh's buffer stack (``push-line``, ``print -z``), whose
        next entry the prompt after the ``cd`` would otherwise put on the line.
        Both append to what is saved already.  Running the init code again
        keeps the saved text.
        The init line is parsed under the user's options, so it uses no pattern
        syntax that an option such as ``sh_glob`` changes.  ``read`` and ``print``
        are named as builtins, so that a function or alias of the user's does not
        take their place and lose saved lines.
        """
        return (
            " setopt HIST_IGNORE_SPACE; "
            "_nn_insert() { REGION_ACTIVE=0; [[ $KEYMAP == main || $KEYMAP == emacs || $KEYMAP == viins ]] || zle -K main }; "
            "_nn_stash() { _nn_insert; emulate -L zsh; local x; _nn_line+=$BUFFER; BUFFER=; "
            'while IFS= builtin read -rz x; do _nn_stack+=("$x"); done }; '
            "_nn_restore() { _nn_insert; emulate -L zsh; local i; "
            'for ((i=$#_nn_stack; i>0; i--)); do builtin print -rz -- "$_nn_stack[i]"; done; _nn_stack=(); '
            "LBUFFER+=$_nn_line; _nn_line= }; "
            "typeset -ga _nn_stack; : ${_nn_line=}; "
            "zle -N _nn_insert; zle -N _nn_stash; zle -N _nn_restore; "
            "_nn_bind() { emulate -L zsh; local map; for map in $keymaps; do case $map in .safe|isearch|command) continue;; esac; bindkey -M $map "
            "$'\\e[95~' _nn_insert $'\\e[96~' _nn_stash $'\\e[94~' _nn_restore "
            "$'\\e[93~' vi-history-search-backward $'\\e[92~' send-break; done }; "
            f"_nn_precmd() {{ {self._hook_body()}; _nn_bind }}; precmd_functions+=(_nn_precmd)\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def set_aside_sequence(self) -> bytes:
        """Stash, open a search and break it off, then stash again.

        A vi search or a named command (``:``, Alt+x) that is waiting for keys
        would take the ``cd`` as its text and run the typed line.  It ignores the
        first stash, and ``send-break`` cancels it with the line intact, which the
        second stash then sets aside.  With nothing waiting, the first stash sets
        the line aside and ``send-break`` cancels the search opened just before,
        on the empty line; ``send-break`` never reaches the editor without one.

        A command waiting for a single key (Ctrl+V, a lone Escape) takes the escape
        byte of the first key, so the stash key still arrives whole.  Its widget
        sets the buffer itself, so no key that zsh-autosuggestions treats as
        accepting a suggestion (such as Ctrl+E) is involved.
        """
        stash = _INSERT_MODE_SEQUENCE + _SET_ASIDE_SEQUENCE
        return stash + _ZSH_SEARCH_SEQUENCE + _ZSH_BREAK_SEQUENCE + stash

    def put_back_sequence(self) -> bytes:
        return _PUT_BACK_SEQUENCE


class BashDriver(ShellDriver):
    """Shell driver for bash."""

    def __init__(self) -> None:
        super().__init__(line_editing=True)

    def init_code(self) -> str:
        """Install the hook and the keys for hidden writes, and turn readline's horizontal scrolling off.

        The key that dismisses a completion question is bound to an empty macro in all three keymaps.

        The hook reports ``noedit`` before a prompt without line editing (``set +o emacs`` and no vi
        mode) and in a bash older than ``_BASH_MIN_EDITING_VERSION``.

        With horizontal scrolling, bash 5.2 can loop forever when the width changes, whatever the
        height.  A bash without line editing only warns about each ``bind``, which is discarded.
        The stash function keeps the line in a variable, not in the kill ring, which stays as it was.
        It appends, so a second stash never overwrites the saved line.
        """
        editing = f"(( BASH_VERSINFO[0] >= {_BASH_MIN_EDITING_VERSION} )) && [[ -o emacs || -o vi ]]"
        hook = f"if {editing}; then {self._hook_body()}; else {self._hook_body('noedit')}; fi"
        # An empty macro does nothing: outside vi command mode there is no mode to change.
        return (
            f" bind 'set horizontal-scroll-mode off' 2>/dev/null; "
            "_nn_stash() { _nn_line+=$READLINE_LINE; READLINE_LINE=; READLINE_POINT=0; }; "
            # A READLINE_POINT beyond the end puts the cursor at the end.
            "_nn_restore() { READLINE_LINE+=${_nn_line-}; _nn_line=; READLINE_POINT=999999; }; "
            "for _nn_map in emacs vi-insert; do "
            """bind -m $_nn_map '"\\e[95~": ""' 2>/dev/null; """
            """bind -m $_nn_map '"\\e[93\\C-g": ""' 2>/dev/null; """
            """bind -m $_nn_map -x '"\\e[96~": _nn_stash' 2>/dev/null; """
            """bind -m $_nn_map -x '"\\e[94~": _nn_restore' 2>/dev/null; done; unset _nn_map; """
            """bind -m vi-command '"\\e[95~": vi-insertion-mode' 2>/dev/null; """
            """bind -m vi-command '"\\e[93\\C-g": ""' 2>/dev/null; """
            f"HISTCONTROL=\"${{HISTCONTROL:+${{HISTCONTROL}}:}}ignorespace\"; _nn_precmd() {{ {hook}; }}; PROMPT_COMMAND=${{PROMPT_COMMAND:+${{PROMPT_COMMAND}}$'\\n'}}_nn_precmd\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def set_aside_sequence(self) -> bytes:
        """Dismiss a completion question, enter a mode that takes text and send the private stash key.

        readline's ``Display all N possibilities?`` and ``--More--`` read single keys.  They would
        ignore the private keys, take the space in front of a hidden ``cd`` as "yes" and run or
        change the typed line.  They ignore the insert mode key, and the dismiss key ends them.  The
        insert mode key goes first: a command that waits for one key (Ctrl+V, a lone Escape) takes
        its first byte, and the keys after it still arrive whole.  It goes out again after the
        dismiss key, because a question asked in vi command mode leaves that mode on.

        Without line editing (``set +o emacs``) this does not remove the typed line, so the terminal
        does not send it while the hook reports ``noedit``.
        """
        return _INSERT_MODE_SEQUENCE + _DISMISS_SEQUENCE + _INSERT_MODE_SEQUENCE + _SET_ASIDE_SEQUENCE

    def put_back_sequence(self) -> bytes:
        """Like ``set_aside_sequence``: input typed while a hidden ``cd`` ran can leave a question waiting."""
        return _INSERT_MODE_SEQUENCE + _DISMISS_SEQUENCE + _INSERT_MODE_SEQUENCE + _PUT_BACK_SEQUENCE


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
