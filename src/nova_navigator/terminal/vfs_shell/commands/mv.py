"""mv command — move/rename files and directories."""

from __future__ import annotations

import argparse

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext


class MvCommand(Command):
    @property
    def name(self) -> str:
        return "mv"

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="mv", add_help=False)
        p.add_argument("sources", nargs="+")
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        *sources, dest_str = args.sources
        if not sources:
            ctx.write_error("mv: missing destination operand\r\n")
            return 1
        dest = ctx.resolve(dest_str)

        exit_code = 0
        for src_str in sources:
            src = ctx.resolve(src_str)
            try:
                ctx.filesystem.rename(src, dest)
            except FileNotFoundError:
                ctx.write_error(f"mv: cannot stat '{src_str}': No such file or directory\r\n")
                exit_code = 1
            except FileExistsError:
                ctx.write_error(f"mv: cannot move '{src_str}' to '{dest_str}': File exists\r\n")
                exit_code = 1
            except OSError as e:
                ctx.write_error(f"mv: cannot move '{src_str}': {e}\r\n")
                exit_code = 1

        return exit_code
