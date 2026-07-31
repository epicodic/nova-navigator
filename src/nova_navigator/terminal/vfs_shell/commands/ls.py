"""ls command — list directory contents."""

from __future__ import annotations

import argparse
import time

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext
from nova_navigator.vfs.types import Stat

_KB = 1024
_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024
_MTIME_WIDTH = len("0000-00-00 00:00")


class LsCommand(Command):
    @property
    def name(self) -> str:
        return "ls"

    @property
    def aliases(self) -> list[str]:
        return ["dir"]

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="ls", add_help=False)
        p.add_argument("-l", "--long", action="store_true", dest="long")
        p.add_argument("-a", "--all", action="store_true", dest="all")
        p.add_argument("-h", "--human-readable", action="store_true", dest="human")
        p.add_argument("--sort", choices=["name", "size", "time"], default="name")
        p.add_argument("paths", nargs="*", default=["."])
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        exit_code = 0
        for path_str in args.paths:
            target = ctx.resolve(path_str)
            try:
                stat = ctx.filesystem.stat(target)
            except FileNotFoundError:
                ctx.write_error(f"ls: cannot access '{path_str}': No such file or directory\r\n")
                exit_code = 2
                continue

            if not stat.is_directory:
                if args.long:
                    ctx.write(self._format_long(target.name, stat, args.human) + "\r\n")
                else:
                    ctx.write(target.name + "\r\n")
                continue

            entries: list[tuple[str, Stat]] = []
            async for entry in ctx.filesystem.iterdir(target):
                if ctx.is_cancelled():
                    return 130
                if not args.all and entry.stat.is_hidden:
                    continue
                entries.append((entry.name, entry.stat))

            if args.sort == "size":
                entries.sort(key=lambda e: e[1].size, reverse=True)
            elif args.sort == "time":
                entries.sort(key=lambda e: e[1].modified, reverse=True)
            else:
                entries.sort(key=lambda e: e[0].lower())

            if args.long:
                for name, st in entries:
                    ctx.write(self._format_long(name, st, args.human) + "\r\n")
            else:
                names = [self._colorize(name, st) for name, st in entries]
                ctx.write("  ".join(names) + "\r\n")

        return exit_code

    def _format_long(self, name: str, stat: Stat, human: bool) -> str:
        """Format a single entry in long listing format."""
        kind = "d" if stat.is_directory else "."
        size = "-" if stat.is_directory else (self._human_size(stat.size) if human else str(stat.size))
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.modified)) if stat.modified >= 0 else " " * _MTIME_WIDTH
        return f"{kind} {size:>8s}  {mtime}  {name}"

    def _human_size(self, size: int) -> str:
        """Format size in human-readable form."""
        if size < _KB:
            return f"{size}B"
        if size < _MB:
            return f"{size / _KB:.1f}K"
        if size < _GB:
            return f"{size / _MB:.1f}M"
        return f"{size / _GB:.1f}G"

    def _colorize(self, name: str, stat: Stat) -> str:
        """Apply ANSI color codes based on file type."""
        if stat.is_directory:
            return f"\033[1;34m{name}\033[0m"
        if stat.is_executable:
            return f"\033[1;32m{name}\033[0m"
        return name
