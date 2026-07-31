"""cd command — change directory."""

from __future__ import annotations

import argparse

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext


class CdCommand(Command):
    @property
    def name(self) -> str:
        return "cd"

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="cd", add_help=False)
        p.add_argument("path", nargs="?", default=None)
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        if args.path is None:
            target = ctx.filesystem.home()
        else:
            target = ctx.resolve(args.path)

        try:
            stat = ctx.filesystem.stat(target)
        except FileNotFoundError:
            ctx.write_error(f"cd: {args.path}: No such file or directory\r\n")
            return 1

        if not stat.is_directory:
            ctx.write_error(f"cd: {args.path}: Not a directory\r\n")
            return 1

        ctx.cwd = target
        return 0
