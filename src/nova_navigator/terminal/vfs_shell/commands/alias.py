"""alias command — list, add, or remove shell aliases."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext

if TYPE_CHECKING:
    from nova_navigator.terminal.vfs_shell.aliases import AliasStore


class AliasCommand(Command):
    """List, add, or remove command aliases.

    Usage::

        alias                    # list all aliases
        alias ll='ls -l'         # define an alias
        alias -r ll              # remove an alias
    """

    def __init__(self, store: AliasStore | None = None) -> None:
        self._store: AliasStore | None = store

    def set_store(self, store: AliasStore) -> None:
        """Set the alias store reference after construction."""
        self._store = store

    @property
    def name(self) -> str:
        return "alias"

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="alias", add_help=False)
        p.add_argument("-r", "--remove", metavar="NAME", help="remove an alias")
        p.add_argument("definition", nargs="*", help="NAME=VALUE")
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        assert self._store is not None

        # Remove mode
        if args.remove:
            if self._store.remove(args.remove):
                return 0
            ctx.write_error(f"alias: {args.remove}: not found\r\n")
            return 1

        # No arguments — list all
        if not args.definition:
            for name, expansion in self._store.items():
                ctx.write(f"alias {name}='{expansion}'\r\n")
            return 0

        # Define aliases: accept "name=value" or "name='value'"
        for defn in args.definition:
            eq = defn.find("=")
            if eq < 1:
                ctx.write_error(f"alias: invalid definition: {defn}\r\n")
                return 1
            alias_name = defn[:eq]
            value = defn[eq + 1 :]
            # Strip surrounding quotes if present
            if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
                value = value[1:-1]
            self._store.set(alias_name, value)

        return 0
