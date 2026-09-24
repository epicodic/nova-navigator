"""Command execution service — run shell commands in a terminal or in the background."""

from .errors import (
    CommandCancelledError,
    CommandError,
    CommandFailedError,
    CommandNotSupportedError,
    TerminalBusyError,
)
from .runner import (
    Command,
    CommandMode,
    CommandResult,
    CommandRunner,
    CommandTerminal,
    JobSink,
    TerminalProvider,
    terminal_line,
)

__all__ = [
    "Command",
    "CommandCancelledError",
    "CommandError",
    "CommandFailedError",
    "CommandMode",
    "CommandNotSupportedError",
    "CommandResult",
    "CommandRunner",
    "CommandTerminal",
    "JobSink",
    "TerminalBusyError",
    "TerminalProvider",
    "terminal_line",
]
