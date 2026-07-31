"""Built-in VFS shell commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nova_navigator.terminal.vfs_shell.command import Command
from nova_navigator.terminal.vfs_shell.commands.alias import AliasCommand
from nova_navigator.terminal.vfs_shell.commands.cat import CatCommand
from nova_navigator.terminal.vfs_shell.commands.cd import CdCommand
from nova_navigator.terminal.vfs_shell.commands.clear import ClearCommand
from nova_navigator.terminal.vfs_shell.commands.cp import CpCommand
from nova_navigator.terminal.vfs_shell.commands.echo import EchoCommand
from nova_navigator.terminal.vfs_shell.commands.head import HeadCommand
from nova_navigator.terminal.vfs_shell.commands.help import HelpCommand
from nova_navigator.terminal.vfs_shell.commands.history import HistoryCommand
from nova_navigator.terminal.vfs_shell.commands.ls import LsCommand
from nova_navigator.terminal.vfs_shell.commands.mkdir import MkdirCommand
from nova_navigator.terminal.vfs_shell.commands.mv import MvCommand
from nova_navigator.terminal.vfs_shell.commands.pwd import PwdCommand
from nova_navigator.terminal.vfs_shell.commands.rm import RmCommand
from nova_navigator.terminal.vfs_shell.commands.tail import TailCommand
from nova_navigator.terminal.vfs_shell.registry import CommandRegistry

if TYPE_CHECKING:
    from nova_navigator.terminal.vfs_shell.aliases import AliasStore


def all_commands() -> list[Command]:
    """Return instances of all built-in commands."""
    return [
        AliasCommand(),
        CatCommand(),
        CdCommand(),
        ClearCommand(),
        CpCommand(),
        EchoCommand(),
        HeadCommand(),
        HelpCommand(),
        HistoryCommand(),
        LsCommand(),
        MkdirCommand(),
        MvCommand(),
        PwdCommand(),
        RmCommand(),
        TailCommand(),
    ]


def register_all(registry: CommandRegistry, alias_store: AliasStore) -> None:
    """Register all built-in commands into the given registry.

    Also wires up the help command's registry reference and the alias
    command's store reference after registration.
    """
    for cmd in all_commands():
        registry.register(cmd)
        if isinstance(cmd, HelpCommand):
            cmd.set_registry(registry)
            cmd.set_alias_store(alias_store)
        if isinstance(cmd, AliasCommand):
            cmd.set_store(alias_store)
