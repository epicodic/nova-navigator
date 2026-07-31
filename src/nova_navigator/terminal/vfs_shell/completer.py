"""Tab completion for VFS shell — commands and file paths."""

from __future__ import annotations

from collections.abc import Callable

from nova_navigator.terminal.vfs_shell.aliases import AliasStore
from nova_navigator.terminal.vfs_shell.registry import CommandRegistry
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.vpath import VPath


class TabCompleter:
    """Provides tab-completion candidates for commands and file paths.

    First token position completes command names from the registry.
    Subsequent positions complete file/directory paths via VFS iterdir().
    """

    def __init__(
        self,
        registry: CommandRegistry,
        filesystem: Filesystem,
        cwd_fn: Callable[[], VPath],
        alias_store: AliasStore | None = None,
    ) -> None:
        self._registry = registry
        self._filesystem = filesystem
        self._cwd_fn = cwd_fn
        self._alias_store = alias_store

    async def complete(self, line: str, cursor: int) -> list[str]:
        """Return sorted completion candidates for the word at cursor.

        Returns full replacement strings for the current word.
        """
        # Find the word boundaries around the cursor
        text = line[:cursor]
        word_start = text.rfind(" ") + 1
        prefix = text[word_start:]

        is_first_token = " " not in text.rstrip()

        if is_first_token:
            return self._complete_command(prefix)
        return await self._complete_path(prefix)

    def word_boundaries(self, line: str, cursor: int) -> tuple[int, int]:
        """Return (start, end) character indices of the word at cursor."""
        text_before = line[:cursor]
        word_start = text_before.rfind(" ") + 1
        # End extends to next space or end of line
        word_end = line.find(" ", cursor)
        if word_end == -1:
            word_end = len(line)
        return word_start, word_end

    def _complete_command(self, prefix: str) -> list[str]:
        """Complete command names and alias names matching prefix."""
        candidates = [cmd.name for cmd in self._registry.all_commands() if cmd.name.startswith(prefix)]
        if self._alias_store is not None:
            candidates.extend(name for name in self._alias_store.names() if name.startswith(prefix))
        return sorted(set(candidates))

    async def _complete_path(self, prefix: str) -> list[str]:
        """Complete file/directory paths matching prefix."""
        cwd = self._cwd_fn()

        # Split into directory part and name part
        if "/" in prefix:
            last_slash = prefix.rfind("/")
            dir_part = prefix[: last_slash + 1]
            name_prefix = prefix[last_slash + 1 :]
            # Resolve the directory to list
            if prefix.startswith("/"):
                target = self._filesystem.path(dir_part)
            else:
                target = self._filesystem.path(cwd.path / dir_part)
        else:
            dir_part = ""
            name_prefix = prefix
            target = cwd

        try:
            entries = [entry async for entry in self._filesystem.iterdir(target)]
        except (FileNotFoundError, NotADirectoryError, OSError):
            return []

        candidates: list[str] = []
        for entry in entries:
            if entry.stat.is_hidden and not name_prefix.startswith("."):
                continue
            if entry.name.startswith(name_prefix):
                suffix = "/" if entry.stat.is_directory else ""
                candidates.append(dir_part + entry.name + suffix)

        return sorted(candidates)
