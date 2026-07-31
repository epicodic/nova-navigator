"""help command — list available commands or show usage for one."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext

if TYPE_CHECKING:
    from nova_navigator.terminal.vfs_shell.registry import CommandRegistry


class HelpCommand(Command):
    """Help command that needs a reference to the registry."""

    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self._registry: CommandRegistry | None = registry

    def set_registry(self, registry: CommandRegistry) -> None:
        """Set the registry reference after construction."""
        self._registry = registry

    @property
    def name(self) -> str:
        return "help"

    @property
    def aliases(self) -> list[str]:
        return ["?"]

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="help", add_help=False)
        p.add_argument("command", nargs="?", default=None)
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        assert self._registry is not None

        if args.command is None:
            ctx.write("Available commands:\r\n")
            for cmd in self._registry.all_commands():
                aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
                ctx.write(f"  {cmd.name}{aliases}\r\n")
            ctx.write("\r\nType 'help <command>' for usage information.\r\n")
            return 0

        cmd = self._registry.get(args.command)
        if cmd is None:
            ctx.write_error(f"help: no help for '{args.command}'\r\n")
            return 1

        parser = cmd.create_parser()
        ctx.write(parser.format_usage())
        return 0
