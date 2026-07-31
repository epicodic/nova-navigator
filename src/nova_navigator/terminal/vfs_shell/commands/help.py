"""help command — list available commands or show usage for one."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext

if TYPE_CHECKING:
    from nova_navigator.terminal.vfs_shell.aliases import AliasStore
    from nova_navigator.terminal.vfs_shell.registry import CommandRegistry


class HelpCommand(Command):
    """Help command that needs a reference to the registry."""

    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self._registry: CommandRegistry | None = registry
        self._alias_store: AliasStore | None = None

    def set_registry(self, registry: CommandRegistry) -> None:
        """Set the registry reference after construction."""
        self._registry = registry

    def set_alias_store(self, store: AliasStore) -> None:
        """Set the alias store reference after construction."""
        self._alias_store = store

    @property
    def name(self) -> str:
        return "help"

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="help", add_help=False)
        p.add_argument("command", nargs="?", default=None)
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        assert self._registry is not None

        if args.command is None:
            ctx.write("Available commands:\r\n")
            # Build reverse alias map: command name -> list of alias names
            alias_map: dict[str, list[str]] = {}
            if self._alias_store is not None:
                for alias_name, expansion in self._alias_store.items():
                    # Only show simple aliases (single-word expansions that
                    # match a command name) as parenthetical hints.
                    parts = expansion.split()
                    if len(parts) == 1:
                        alias_map.setdefault(expansion, []).append(alias_name)
            for cmd in self._registry.all_commands():
                names = alias_map.get(cmd.name)
                suffix = f" ({', '.join(names)})" if names else ""
                ctx.write(f"  {cmd.name}{suffix}\r\n")
            ctx.write("\r\nType 'help <command>' for usage information.\r\n")
            ctx.write("Type 'alias' to see all aliases.\r\n")
            return 0

        cmd = self._registry.get(args.command)
        if cmd is None:
            ctx.write_error(f"help: no help for '{args.command}'\r\n")
            return 1

        parser = cmd.create_parser()
        ctx.write(parser.format_usage())
        return 0
