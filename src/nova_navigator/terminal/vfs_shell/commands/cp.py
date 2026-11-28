"""cp command — copy files and directories."""

from __future__ import annotations

import argparse

from nova_navigator.filemanager.tasks import is_into_itself, would_overwrite_source
from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext
from nova_navigator.vfs.vpath import VPath


class CpCommand(Command):
    mutates_filesystem = True

    @property
    def name(self) -> str:
        return "cp"

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="cp", add_help=False)
        p.add_argument("-r", "-R", "--recursive", action="store_true", dest="recursive")
        p.add_argument("sources", nargs="+")
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        *sources, dest_str = args.sources
        if not sources:
            ctx.write_error("cp: missing destination operand\r\n")
            return 1
        dest = ctx.resolve(dest_str)

        exit_code = 0
        for src_str in sources:
            src = ctx.resolve(src_str)
            try:
                stat = ctx.filesystem.stat(src)
            except FileNotFoundError:
                ctx.write_error(f"cp: cannot stat '{src_str}': No such file or directory\r\n")
                exit_code = 1
                continue

            if stat.is_symlink and args.recursive:
                result = self._cp_link(src, dest, src_str, ctx)
            elif stat.is_directory:
                if not args.recursive:
                    ctx.write_error(f"cp: -r not specified; omitting directory '{src_str}'\r\n")
                    exit_code = 1
                    continue
                if is_into_itself(src, dest):
                    ctx.write_error(f"cp: cannot copy a directory, '{src_str}', into itself, '{dest_str}'\r\n")
                    exit_code = 1
                    continue
                result = await self._cp_tree(src, dest, src_str, ctx)
                if result != 0:
                    exit_code = result
            else:
                result = self._cp_file(src, dest, src_str, ctx)
                if result != 0:
                    exit_code = result

        return exit_code

    def _cp_link(self, src: VPath, dest: VPath, src_str: str, ctx: ShellContext) -> int:
        """Copy the link *src* as a link when the filesystem has links, else copy a file target or skip it."""
        fs = ctx.filesystem
        if fs.supports_symlinks():
            try:
                fs.symlink(dest, fs.readlink(src))
            except OSError as e:
                ctx.write_error(f"cp: cannot create symbolic link '{dest.path}': {e}\r\n")
                return 1
            return 0
        stat = src.stat
        if not stat.is_directory and not stat.is_broken_symlink:
            return self._cp_file(src, dest, src_str, ctx)
        ctx.write_error(f"cp: skipping link '{src_str}': symbolic links are not supported on this filesystem\r\n")
        return 1

    def _cp_file(self, src: VPath, dest: VPath, src_str: str, ctx: ShellContext) -> int:
        """Copy a single file."""
        if would_overwrite_source(src, dest):
            ctx.write_error(f"cp: '{src_str}' and '{dest.path}' are the same file\r\n")
            return 1
        try:
            reader = ctx.filesystem.read(src)
        except OSError as e:
            ctx.write_error(f"cp: cannot open '{src_str}': {e}\r\n")
            return 1

        try:
            writer = ctx.filesystem.write(dest)
            try:
                while True:
                    chunk = reader.read(65536)
                    if not chunk:
                        break
                    writer.write(chunk)
            finally:
                writer.close()
        except OSError as e:
            ctx.write_error(f"cp: cannot create '{dest.path}': {e}\r\n")
            return 1
        finally:
            reader.close()
        return 0

    async def _cp_tree(self, src: VPath, dest: VPath, src_str: str, ctx: ShellContext) -> int:
        """Recursively copy a directory tree."""
        try:
            ctx.filesystem.mkdir(dest)
        except FileExistsError:
            pass
        except OSError as e:
            ctx.write_error(f"cp: cannot create directory '{dest.path}': {e}\r\n")
            return 1

        failed = 0
        try:
            async for entry in ctx.filesystem.iterdir(src):
                if ctx.is_cancelled():
                    return 130
                child_dest = ctx.filesystem.path(dest.path / entry.name)
                if entry.stat.is_symlink:
                    failed = self._cp_link(entry, child_dest, f"{src_str}/{entry.name}", ctx) or failed
                elif entry.stat.is_directory:
                    result = await self._cp_tree(entry, child_dest, f"{src_str}/{entry.name}", ctx)
                    if result != 0:
                        return result
                else:
                    result = self._cp_file(entry, child_dest, f"{src_str}/{entry.name}", ctx)
                    if result != 0:
                        return result
        except OSError as e:
            ctx.write_error(f"cp: cannot read '{src_str}': {e}\r\n")
            return 1
        return failed
