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
import re
from abc import ABC, abstractmethod
from pathlib import PurePath

from nova_navigator.terminal.shell_editor_protocol import (
    PROBE_SEQUENCE,
    RESTORE_SEQUENCE,
    STASH_SEQUENCE,
    EditorOperation,
)

_logger = logging.getLogger(__name__)

_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/._-"
)
_NONCE_RE = re.compile(r"^[0-9a-f]+$")

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


def _validate_editor_nonce(nonce: str) -> str:
    """Validate the nonce used in generated shell editor integration code."""
    if _NONCE_RE.fullmatch(nonce) is None:
        raise ValueError("editor nonce must contain only lowercase hex characters")
    return nonce


class ShellDriver(ABC):
    """Abstract base class for shell-specific terminal integration."""

    def __init__(self, *, prompt_ready: bool) -> None:
        self._prompt_ready = prompt_ready

    @property
    def supports_prompt_ready(self) -> bool:
        """True if init_code() installs an OSC 133;B prompt-end hook."""
        return self._prompt_ready

    @property
    def supports_editor_protocol(self) -> bool:
        """True if editor integration can be installed for this shell."""
        return False

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

    def editor_integration_code(self, nonce: str) -> str:
        """Return shell code that installs private editor protocol bindings."""
        _ = nonce
        return ""

    def startup_code(self, nonce: str) -> str:
        """Return init and editor integration code as one command line.

        A single line means the shell executes both parts in one command cycle,
        so startup draining can hide the whole echo and end on the precmd that
        follows the READY acknowledgement.

        Args:
            nonce: The per-session nonce used to namespace editor protocol
                identifiers.

        Returns:
            A string of shell code ending with a newline.
        """
        init = self.init_code()
        editor = self.editor_integration_code(nonce)
        if not editor:
            return init
        return init.rstrip("\n") + "; " + editor

    def editor_request(self, operation: EditorOperation) -> bytes:
        """Return the private input sequence for an editor protocol request."""
        if operation is EditorOperation.PROBE:
            return PROBE_SEQUENCE
        if operation is EditorOperation.STASH:
            return STASH_SEQUENCE
        if operation is EditorOperation.RESTORE:
            return RESTORE_SEQUENCE
        raise ValueError(
            "READY is emitted by shell integration and cannot be requested"
        )


class ZshDriver(ShellDriver):
    """Shell driver for zsh."""

    def __init__(self) -> None:
        super().__init__(prompt_ready=True)

    def init_code(self) -> str:
        zle_hook = " autoload -Uz add-zle-hook-widget; _nn_zle_init() { printf '\\033]133;B\\007' >/dev/tty }; add-zle-hook-widget -Uz zle-line-init _nn_zle_init"
        return f" setopt HIST_IGNORE_SPACE; _nn_precmd() {{ {self._hook_body()} }}; precmd_functions+=(_nn_precmd);{zle_hook}\n"

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    @property
    def supports_editor_protocol(self) -> bool:
        return True

    def editor_integration_code(self, nonce: str) -> str:
        nonce = _validate_editor_nonce(nonce)
        seq_var = f"_nn_seq_{nonce}"
        emit_fn = f"_nn_emit_{nonce}"
        probe_widget = f"_nn_probe_{nonce}"
        stash_widget = f"_nn_stash_{nonce}"
        restore_widget = f"_nn_restore_{nonce}"
        stash_buffer_var = f"_nn_stash_buffer_{nonce}"
        stash_cursor_var = f"_nn_stash_cursor_{nonce}"

        return (
            f"typeset -g {seq_var}=0;"
            f'{emit_fn}() {{ printf \'\\033]777;nn;{nonce};%s;%s;%s;%s\\007\' "$1" "${seq_var}" "$2" "$3" >/dev/tty; }};'
            f"{probe_widget}() {{ (( {seq_var} += 1 )); local len=${{#BUFFER}}; local cur=$CURSOR;"
            f' {emit_fn} probe "$len" "$cur"; }};'
            f'{stash_widget}() {{ (( {seq_var} += 1 )); typeset -g {stash_buffer_var}="$BUFFER";'
            f" typeset -g {stash_cursor_var}=$CURSOR; BUFFER=''; CURSOR=0; {emit_fn} stash 0 0; }};"
            f'{restore_widget}() {{ (( {seq_var} += 1 )); BUFFER="${{{stash_buffer_var}-}}";'
            f" CURSOR=${{{stash_cursor_var}-0}}; local len=${{#BUFFER}}; local cur=$CURSOR;"
            f' unset {stash_buffer_var} {stash_cursor_var}; {emit_fn} restore "$len" "$cur"; }};'
            f"zle -N {probe_widget}; zle -N {stash_widget}; zle -N {restore_widget};"
            f" bindkey -M emacs '^[[99~' {probe_widget};"
            f" bindkey -M viins '^[[99~' {probe_widget};"
            f" bindkey -M vicmd '^[[99~' {probe_widget};"
            f" bindkey -M emacs '^[[98~' {stash_widget};"
            f" bindkey -M viins '^[[98~' {stash_widget};"
            f" bindkey -M vicmd '^[[98~' {stash_widget};"
            f" bindkey -M emacs '^[[97~' {restore_widget};"
            f" bindkey -M viins '^[[97~' {restore_widget};"
            f" bindkey -M vicmd '^[[97~' {restore_widget};"
            f" printf '\\033]777;nn;{nonce};ready;0;0;0\\007' >/dev/tty\n"
        )


class BashDriver(ShellDriver):
    """Shell driver for bash."""

    def __init__(self) -> None:
        super().__init__(prompt_ready=True)

    def init_code(self) -> str:
        return (
            ' HISTCONTROL="${HISTCONTROL:+${HISTCONTROL}:}ignorespace";'
            f" _nn_precmd() {{ {self._hook_body()}; }};"
            " PROMPT_COMMAND=${PROMPT_COMMAND:+${PROMPT_COMMAND}$'\\n'}_nn_precmd;"
            " PS1=\"${PS1}\"$'\\[\\033]133;B\\007\\]'\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    @property
    def supports_editor_protocol(self) -> bool:
        return True

    def editor_integration_code(self, nonce: str) -> str:
        nonce = _validate_editor_nonce(nonce)
        seq_var = f"_nn_seq_{nonce}"
        emit_fn = f"_nn_emit_{nonce}"
        probe_fn = f"_nn_probe_{nonce}"
        stash_fn = f"_nn_stash_{nonce}"
        restore_fn = f"_nn_restore_{nonce}"
        stash_line_var = f"_nn_stash_line_{nonce}"
        stash_point_var = f"_nn_stash_point_{nonce}"

        return (
            f"{seq_var}=0;"
            f'{emit_fn}() {{ printf \'\\033]777;nn;{nonce};%s;%s;%s;%s\\007\' "$1" "${seq_var}" "$2" "$3" >/dev/tty; }};'
            f"{probe_fn}() {{ (( {seq_var} += 1 )); local len=${{#READLINE_LINE}}; local cur=${{READLINE_POINT:-0}};"
            f' {emit_fn} probe "$len" "$cur"; }};'
            f'{stash_fn}() {{ (( {seq_var} += 1 )); {stash_line_var}="$READLINE_LINE"; {stash_point_var}=${{READLINE_POINT:-0}};'
            f" READLINE_LINE=''; READLINE_POINT=0; {emit_fn} stash 0 0; }};"
            f'{restore_fn}() {{ (( {seq_var} += 1 )); READLINE_LINE="${{{stash_line_var}-}}";'
            f' READLINE_POINT="${{{stash_point_var}-0}}"; local len=${{#READLINE_LINE}}; local cur=${{READLINE_POINT:-0}};'
            f' unset {stash_line_var} {stash_point_var}; {emit_fn} restore "$len" "$cur"; }};'
            f"bind -m emacs-standard -x '\"\\e[99~\":{probe_fn}';"
            f"bind -m emacs-meta -x '\"\\e[99~\":{probe_fn}';"
            f"bind -m emacs-ctlx -x '\"\\e[99~\":{probe_fn}';"
            f"bind -m vi-insert -x '\"\\e[99~\":{probe_fn}';"
            f"bind -m vi-command -x '\"\\e[99~\":{probe_fn}';"
            f"bind -m emacs-standard -x '\"\\e[98~\":{stash_fn}';"
            f"bind -m emacs-meta -x '\"\\e[98~\":{stash_fn}';"
            f"bind -m emacs-ctlx -x '\"\\e[98~\":{stash_fn}';"
            f"bind -m vi-insert -x '\"\\e[98~\":{stash_fn}';"
            f"bind -m vi-command -x '\"\\e[98~\":{stash_fn}';"
            f"bind -m emacs-standard -x '\"\\e[97~\":{restore_fn}';"
            f"bind -m emacs-meta -x '\"\\e[97~\":{restore_fn}';"
            f"bind -m emacs-ctlx -x '\"\\e[97~\":{restore_fn}';"
            f"bind -m vi-insert -x '\"\\e[97~\":{restore_fn}';"
            f"bind -m vi-command -x '\"\\e[97~\":{restore_fn}';"
            f"printf '\\033]777;nn;{nonce};ready;0;0;0\\007' >/dev/tty\n"
        )


class FallbackDriver(ShellDriver):
    """Shell driver for generic POSIX sh.

    No SIGSTOP/SIGCONT synchronisation.  Uses PS1 substitution for the hook;
    printf must redirect to /dev/tty to avoid the OSC 7 sequence polluting
    the prompt text.
    """

    def __init__(self) -> None:
        super().__init__(prompt_ready=False)

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
