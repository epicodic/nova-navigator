"""pwd command — print working directory."""

from __future__ import annotations

import argparse

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext


class PwdCommand(Command):
    @property
    def name(self) -> str:
        return "pwd"

    def create_parser(self) -> ShellArgumentParser:
        return ShellArgumentParser(prog="pwd", add_help=False)

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        ctx.write(f"{ctx.cwd.path}\r\n")
        return 0
