"""VFS shell interpreter — tokenize, expand globs, dispatch commands."""

from __future__ import annotations

from collections.abc import Callable

from nova_navigator.terminal.vfs_shell.command import CommandParseError, ShellContext
from nova_navigator.terminal.vfs_shell.commands import register_all
from nova_navigator.terminal.vfs_shell.glob import expand_globs
from nova_navigator.terminal.vfs_shell.registry import CommandRegistry
from nova_navigator.terminal.vfs_shell.tokenizer import tokenize
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.vpath import VPath


class VfsShellInterpreter:
    """Orchestrates tokenization, glob expansion, and command dispatch."""

    def __init__(self, filesystem: Filesystem, cwd: VPath, cols: int, rows: int) -> None:
        self._filesystem = filesystem
        self._cwd = cwd
        self._cols = cols
        self._rows = rows
        self._registry = CommandRegistry()
        self._cancelled = False
        self._register_builtins()

    def _register_builtins(self) -> None:
        """Import and register all built-in commands."""
        register_all(self._registry)

    @property
    def cwd(self) -> VPath:
        """Current working directory."""
        return self._cwd

    @cwd.setter
    def cwd(self, path: VPath) -> None:
        self._cwd = path

    @property
    def cols(self) -> int:
        return self._cols

    @cols.setter
    def cols(self, value: int) -> None:
        self._cols = value

    @property
    def rows(self) -> int:
        return self._rows

    @rows.setter
    def rows(self, value: int) -> None:
        self._rows = value

    @property
    def prompt(self) -> str:
        """The shell prompt string."""
        return f"{self._cwd.compact_path_str}$ "

    @property
    def registry(self) -> CommandRegistry:
        """The command registry (for tab completion)."""
        return self._registry

    def cancel(self) -> None:
        """Signal cancellation to a running command."""
        self._cancelled = True

    async def execute(
        self,
        line: str,
        write_fn: Callable[[str], None],
        write_error_fn: Callable[[str], None],
    ) -> int:
        """Tokenize, expand globs, look up command, parse args, execute.

        Returns exit code (0 = success).
        """
        self._cancelled = False

        tokens = tokenize(line)
        if not tokens:
            return 0

        # Expand globs on all tokens except the command name
        command_name = tokens[0].value
        arg_tokens = tokens[1:]

        expanded_args = await expand_globs(arg_tokens, self._filesystem, self._cwd)

        # Look up command
        cmd = self._registry.get(command_name)
        if cmd is None:
            write_error_fn(f"{command_name}: command not found\r\n")
            return 127

        # Parse arguments
        parser = cmd.create_parser()
        try:
            args = parser.parse_args(expanded_args)
        except CommandParseError as e:
            write_error_fn(f"{e}\r\n")
            return 2

        # Build context
        ctx = ShellContext(
            filesystem=self._filesystem,
            cwd=self._cwd,
            cols=self._cols,
            rows=self._rows,
            write_fn=write_fn,
            write_error_fn=write_error_fn,
            cancel_fn=lambda: self._cancelled,
        )

        # Execute
        exit_code = await cmd.execute(args, ctx)

        # Commands that change directory update ctx.cwd — sync back to interpreter
        self._cwd = ctx.cwd

        return exit_code
