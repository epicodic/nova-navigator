"""Errors raised by the command execution service."""


class CommandError(Exception):
    """Base class for command execution errors."""


class TerminalBusyError(CommandError):
    """The target terminal is running a command or cannot accept one right now."""


class CommandNotSupportedError(CommandError):
    """The filesystem of the working directory cannot run commands."""


class CommandCancelledError(CommandError):
    """The user cancelled a background command."""


class CommandFailedError(CommandError):
    """A background command could not be run (e.g. the connection failed)."""
