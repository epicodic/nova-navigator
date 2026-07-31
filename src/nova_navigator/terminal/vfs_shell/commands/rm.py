"""rm command — remove files and directories."""

from __future__ import annotations

import argparse

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext
from nova_navigator.vfs.vpath import VPath


class RmCommand(Command):
    @property
    def name(self) -> str:
        return "rm"

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="rm", add_help=False)
        p.add_argument("-r", "-R", "--recursive", action="store_true", dest="recursive")
        p.add_argument("-f", "--force", action="store_true", dest="force")
        p.add_argument("paths", nargs="+")
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        exit_code = 0
        for path_str in args.paths:
            target = ctx.resolve(path_str)
            try:
                stat = ctx.filesystem.stat(target)
            except FileNotFoundError:
                if not args.force:
                    ctx.write_error(f"rm: cannot remove '{path_str}': No such file or directory\r\n")
                    exit_code = 1
                continue

            if stat.is_directory:
                if not args.recursive:
                    ctx.write_error(f"rm: cannot remove '{path_str}': Is a directory\r\n")
                    exit_code = 1
                    continue
                result = await self._rm_tree(target, path_str, ctx)
                if result != 0:
                    exit_code = result
            else:
                try:
                    ctx.filesystem.remove(target)
                except OSError as e:
                    if not args.force:
                        ctx.write_error(f"rm: cannot remove '{path_str}': {e}\r\n")
                        exit_code = 1

        return exit_code

    async def _rm_tree(self, path: VPath, display_path: str, ctx: ShellContext) -> int:
        """Recursively remove a directory tree."""
        try:
            async for entry in ctx.filesystem.iterdir(path):
                if ctx.is_cancelled():
                    return 130
                if entry.stat.is_directory:
                    result = await self._rm_tree(entry, f"{display_path}/{entry.name}", ctx)
                    if result != 0:
                        return result
                else:
                    ctx.filesystem.remove(entry)
            ctx.filesystem.rmdir(path)
        except OSError as e:
            ctx.write_error(f"rm: cannot remove '{display_path}': {e}\r\n")
            return 1
        return 0
