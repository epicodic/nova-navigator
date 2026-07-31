"""clear command — clear the terminal screen."""

from __future__ import annotations

import argparse

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext


class ClearCommand(Command):
    @property
    def name(self) -> str:
        return "clear"

    def create_parser(self) -> ShellArgumentParser:
        return ShellArgumentParser(prog="clear", add_help=False)

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        ctx.write("\033[2J\033[H")
        return 0
