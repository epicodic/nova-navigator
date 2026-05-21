"""TerminalPool — manages visibility of all Terminal widgets by filesystem."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems import LocalFilesystem

if TYPE_CHECKING:
    from nova_navigator.terminal.terminal import Terminal

TerminalFactory = Callable[[Filesystem], "Terminal"]

_FactoryEntry = tuple[Callable[[Filesystem], bool], TerminalFactory]


class TerminalPool:
    """Manages one Terminal widget per filesystem connection.

    All terminals are mounted in the Textual DOM simultaneously.
    Only the active terminal is visible (display=True).
    Others are hidden (display=False) but continue running.
    """

    def __init__(self, local_terminal: Terminal | None = None) -> None:
        self._local: Terminal | None = local_terminal
        self._active: Terminal | None = local_terminal
        self._terminals: dict[int, Terminal] = (
            {id(LocalFilesystem.singleton()): local_terminal} if local_terminal is not None else {}
        )
        self._factories: list[_FactoryEntry] = []

    def set_local(self, terminal: Terminal) -> None:
        """Set the local terminal. Called once from compose() after widget creation."""
        self._local = terminal
        self._active = terminal
        self._terminals[id(LocalFilesystem.singleton())] = terminal

    def register_factory(
        self,
        predicate: Callable[[Filesystem], bool],
        factory: TerminalFactory,
    ) -> None:
        """Register a factory for filesystems matching *predicate*."""
        self._factories.append((predicate, factory))

    def register(self, fs: Filesystem, terminal: Terminal) -> None:
        """Associate an already-created terminal with *fs*."""
        self._terminals[id(fs.unwrap())] = terminal

    def has_terminal(self, fs: Filesystem) -> bool:
        """Return True if a terminal is already registered for *fs*."""
        return id(fs.unwrap()) in self._terminals

    def create_for(self, fs: Filesystem) -> Terminal | None:
        """Create a new terminal for *fs* using the registered factory.

        Returns None if no factory matches the resolved filesystem type.
        Does not register the terminal — call register() after mounting.
        """
        resolved = fs.unwrap()
        for predicate, factory in self._factories:
            if predicate(resolved):
                return factory(resolved)
        return None

    def switch_to(self, fs: Filesystem) -> None:
        """Show the terminal for *fs*, hiding the currently active one.

        Falls back to the local terminal if *fs* is not registered.
        Is a no-op if *fs* maps to the already-active terminal.
        """
        resolved = fs.unwrap()
        terminal = self._terminals.get(id(resolved), self._local)
        if terminal is self._active:
            return
        assert self._active is not None
        assert terminal is not None
        terminal.styles.width = self._active.styles.width
        terminal.styles.height = self._active.styles.height
        self._active.display = False
        terminal.display = True
        self._active = terminal

    @property
    def active_terminal(self) -> Terminal:
        """The currently visible terminal."""
        assert self._active is not None, "TerminalPool.set_local() not yet called"
        return self._active

    def all_terminals(self) -> Iterable[Terminal]:
        """Iterate over all registered terminals (local + remote)."""
        return self._terminals.values()

    async def stop_all(self) -> None:
        """Stop all terminals. Call during app teardown."""
        for terminal in self._terminals.values():
            terminal.stop()
