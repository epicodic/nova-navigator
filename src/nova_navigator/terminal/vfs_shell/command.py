"""Command ABC, ShellContext, and argument parser for VFS shell."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from collections.abc import Callable

    from nova_navigator.vfs.filesystem import Filesystem
    from nova_navigator.vfs.vpath import VPath


class CommandParseError(Exception):
    """Raised by ShellArgumentParser instead of calling sys.exit()."""


class ShellArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that raises CommandParseError instead of exiting."""

    def error(self, message: str) -> NoReturn:
        raise CommandParseError(f"{self.format_usage().rstrip()}\n{self.prog}: error: {message}")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise CommandParseError(message or "")


class ShellContext:
    """Execution environment passed to every command."""

    def __init__(
        self,
        filesystem: Filesystem,
        cwd: VPath,
        cols: int,
        rows: int,
        write_fn: Callable[[str], None],
        write_error_fn: Callable[[str], None],
        cancel_fn: Callable[[], bool],
        history: list[str] | None = None,
    ) -> None:
        self.filesystem = filesystem
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.history: list[str] = history if history is not None else []
        self._write_fn = write_fn
        self._write_error_fn = write_error_fn
        self._cancel_fn = cancel_fn

    def resolve(self, path_str: str) -> VPath:
        """Resolve a path string relative to cwd. Handles '.', '..', absolute."""
        if path_str.startswith("/"):
            resolved = PurePosixPath(path_str)
        elif path_str == "~" or path_str.startswith("~/"):
            home = self.filesystem.home()
            if path_str == "~":
                resolved = home.path
            else:
                resolved = home.path / path_str[2:]
        else:
            resolved = self.cwd.path / path_str

        # Normalize . and ..
        parts: list[str] = []
        for part in resolved.parts:
            if part == ".":
                continue
            if part == "..":
                if parts and parts[-1] != "/":
                    parts.pop()
            else:
                parts.append(part)
        if not parts:
            parts = ["/"]
        normalized = PurePosixPath(*parts) if len(parts) > 1 else PurePosixPath(parts[0])
        return self.filesystem.path(normalized)

    def write(self, text: str) -> None:
        """Write text to terminal output."""
        self._write_fn(text)

    def write_error(self, msg: str) -> None:
        """Write error message (displayed in red)."""
        self._write_error_fn(msg)

    def is_cancelled(self) -> bool:
        """Return True if the user pressed Ctrl+C during execution."""
        return self._cancel_fn()


class Command(ABC):
    """Base class for VFS shell commands."""

    @property
    @abstractmethod
    def name(self) -> str:
        """The primary command name (e.g. 'ls')."""

    @abstractmethod
    def create_parser(self) -> ShellArgumentParser:
        """Return a configured argument parser for this command."""

    @abstractmethod
    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        """Run the command. Return exit code (0 = success)."""
