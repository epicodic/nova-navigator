"""history command — list previously entered commands."""

from __future__ import annotations

import argparse

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext


class HistoryCommand(Command):
    @property
    def name(self) -> str:
        return "history"

    def create_parser(self) -> ShellArgumentParser:
        return ShellArgumentParser(prog="history", add_help=False)

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        for i, entry in enumerate(ctx.history, 1):
            ctx.write(f"{i:5d}  {entry}\r\n")
        return 0
