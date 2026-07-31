"""Command registry for VFS shell."""

from __future__ import annotations

from nova_navigator.terminal.vfs_shell.command import Command


class CommandRegistry:
    """Stores and looks up Command instances by name and alias."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._all: list[Command] = []

    def register(self, cmd: Command) -> None:
        """Register a command by its name."""
        if cmd in self._all:
            return
        self._commands[cmd.name] = cmd
        self._all.append(cmd)

    def get(self, name: str) -> Command | None:
        """Look up a command by name or alias. Returns None if not found."""
        return self._commands.get(name)

    def all_commands(self) -> list[Command]:
        """Return all registered commands sorted by name."""
        return sorted(self._all, key=lambda c: c.name)
