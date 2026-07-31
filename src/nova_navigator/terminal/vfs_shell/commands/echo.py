"""echo command — write arguments to output."""

from __future__ import annotations

import argparse

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext


class EchoCommand(Command):
    @property
    def name(self) -> str:
        return "echo"

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="echo", add_help=False)
        p.add_argument("args", nargs="*")
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        ctx.write(" ".join(args.args) + "\r\n")
        return 0
