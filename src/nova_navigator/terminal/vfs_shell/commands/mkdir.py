"""mkdir command — create directories."""

from __future__ import annotations

import argparse
import contextlib
from pathlib import PurePosixPath

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext
from nova_navigator.vfs.vpath import VPath


class MkdirCommand(Command):
    @property
    def name(self) -> str:
        return "mkdir"

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="mkdir", add_help=False)
        p.add_argument("-p", "--parents", action="store_true", dest="parents")
        p.add_argument("paths", nargs="+")
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        exit_code = 0
        for path_str in args.paths:
            target = ctx.resolve(path_str)
            if args.parents:
                result = self._mkdir_parents(target, path_str, ctx)
                if result != 0:
                    exit_code = result
            else:
                try:
                    ctx.filesystem.mkdir(target)
                except FileExistsError:
                    ctx.write_error(f"mkdir: cannot create directory '{path_str}': File exists\r\n")
                    exit_code = 1
                except FileNotFoundError:
                    ctx.write_error(f"mkdir: cannot create directory '{path_str}': No such file or directory\r\n")
                    exit_code = 1
        return exit_code

    def _mkdir_parents(self, target: VPath, path_str: str, ctx: ShellContext) -> int:
        """Create directory and all parent directories."""
        parts_to_create: list[PurePosixPath] = []
        current = target.path
        while True:
            try:
                stat = ctx.filesystem.stat(ctx.filesystem.path(current))
                if not stat.is_directory:
                    ctx.write_error(f"mkdir: cannot create directory '{path_str}': Not a directory\r\n")
                    return 1
                break
            except FileNotFoundError:
                parts_to_create.append(current)
                parent = current.parent
                if parent == current:
                    break
                current = parent

        for part in reversed(parts_to_create):
            with contextlib.suppress(FileExistsError):
                ctx.filesystem.mkdir(ctx.filesystem.path(part))
        return 0
