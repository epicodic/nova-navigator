"""head command — display first lines of a file."""

from __future__ import annotations

import argparse

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext


class HeadCommand(Command):
    @property
    def name(self) -> str:
        return "head"

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="head", add_help=False)
        p.add_argument("-n", "--lines", type=int, default=10, dest="lines")
        p.add_argument("files", nargs="+")
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        exit_code = 0
        for file_str in args.files:
            target = ctx.resolve(file_str)
            try:
                reader = ctx.filesystem.read(target)
            except FileNotFoundError:
                ctx.write_error(f"head: cannot open '{file_str}': No such file or directory\r\n")
                exit_code = 1
                continue
            except IsADirectoryError:
                ctx.write_error(f"head: error reading '{file_str}': Is a directory\r\n")
                exit_code = 1
                continue

            try:
                data = b""
                while True:
                    chunk = reader.read(8192)
                    if not chunk:
                        break
                    data += chunk
            finally:
                reader.close()

            text = data.decode("utf-8", errors="replace")
            lines = text.split("\n")
            for line in lines[: args.lines]:
                ctx.write(f"{line}\r\n")

        return exit_code
