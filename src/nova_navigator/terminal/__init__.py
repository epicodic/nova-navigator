"""Terminal sub-package — PTY-backed terminal emulator for Textual.

This package provides the ``Terminal`` widget and its supporting abstractions:

- ``PtyBackend`` / ``LocalPtyBackend`` / ``SshPtyBackend`` — PTY process backends.
- ``ShellDriver`` / ``ZshDriver`` / ``BashDriver`` / ``FallbackDriver`` — shell-specific
  hook installation, argument quoting, and precmd parsing.
- ``detect_driver()`` — auto-detect the appropriate driver from a command string.

Architecture overview: see ``docs/terminal.md``.
"""

from nova_navigator.terminal.pty_backend import LocalPtyBackend, PtyBackend
from nova_navigator.terminal.shell_driver import (
    BashDriver,
    FallbackDriver,
    ShellDriver,
    ZshDriver,
    detect_driver,
)
from nova_navigator.terminal.ssh_pty_backend import SshPtyBackend
from nova_navigator.terminal.terminal import Terminal, TerminalDisplay, TerminalPyteScreen
from nova_navigator.terminal.terminal_pool import TerminalPool

__all__ = [
    "BashDriver",
    "FallbackDriver",
    "LocalPtyBackend",
    "PtyBackend",
    "ShellDriver",
    "SshPtyBackend",
    "Terminal",
    "TerminalDisplay",
    "TerminalPool",
    "TerminalPyteScreen",
    "ZshDriver",
    "detect_driver",
]
