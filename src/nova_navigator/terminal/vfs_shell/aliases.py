"""Alias store for the VFS shell."""

from __future__ import annotations

# Default aliases, mapping short names to their expansion strings.
DEFAULT_ALIASES: dict[str, str] = {
    "dir": "ls",
    "ll": "ls -l",
    "la": "ls -la",
    "?": "help",
}


class AliasStore:
    """Mutable mapping of alias names to expansion strings.

    An alias maps a single name (e.g. ``ll``) to a full command string
    (e.g. ``ls -l``).  When the interpreter encounters an alias as the
    first token, it replaces it with the expansion tokens and appends
    any extra arguments the user typed after the alias.
    """

    def __init__(self, defaults: dict[str, str] | None = None) -> None:
        self._aliases: dict[str, str] = dict(defaults) if defaults else {}

    def get(self, name: str) -> str | None:
        """Return the expansion for *name*, or ``None``."""
        return self._aliases.get(name)

    def set(self, name: str, expansion: str) -> None:
        """Create or overwrite an alias."""
        self._aliases[name] = expansion

    def remove(self, name: str) -> bool:
        """Remove an alias. Returns True if it existed."""
        if name in self._aliases:
            del self._aliases[name]
            return True
        return False

    def items(self) -> list[tuple[str, str]]:
        """Return all aliases sorted by name."""
        return sorted(self._aliases.items())

    def names(self) -> list[str]:
        """Return all alias names sorted."""
        return sorted(self._aliases)
