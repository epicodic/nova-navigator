"""cat command — concatenate and display file contents."""

from __future__ import annotations

import argparse

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext


class CatCommand(Command):
    @property
    def name(self) -> str:
        return "cat"

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="cat", add_help=False)
        p.add_argument("-n", "--number", action="store_true", dest="number")
        p.add_argument("files", nargs="+")
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        exit_code = 0
        for file_str in args.files:
            target = ctx.resolve(file_str)
            try:
                reader = ctx.filesystem.read(target)
            except FileNotFoundError:
                ctx.write_error(f"cat: {file_str}: No such file or directory\r\n")
                exit_code = 1
                continue
            except IsADirectoryError:
                ctx.write_error(f"cat: {file_str}: Is a directory\r\n")
                exit_code = 1
                continue

            try:
                data = b""
                while True:
                    chunk = reader.read(8192)
                    if not chunk:
                        break
                    data += chunk
                    if ctx.is_cancelled():
                        return 130
            finally:
                reader.close()

            text = data.decode("utf-8", errors="replace")
            lines = text.split("\n")
            if lines and lines[-1] == "":
                lines = lines[:-1]

            for i, line in enumerate(lines, 1):
                if ctx.is_cancelled():
                    return 130
                if args.number:
                    ctx.write(f"{i:6d}  {line}\r\n")
                else:
                    ctx.write(f"{line}\r\n")

        return exit_code
