"""UserMenuStore — locate, create and (re)load the user menu file."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .evaluate import CompiledMenu, compile_menu
from .model import MenuConfigError, parse_menu

USER_MENU_FILENAME = "usermenu.toml"
DEFAULT_MENU_PATH = Path(__file__).parent.parent / "_default" / USER_MENU_FILENAME


@dataclass(frozen=True)
class LoadResult:
    """The current menu and the problems found while (re)loading it.

    ``messages`` is empty when the file did not change since the last load.
    """

    menu: CompiledMenu
    messages: tuple[str, ...]


class UserMenuStore:
    """Owns the user menu file: creates it from the default and reloads it when it changes."""

    def __init__(self, path: Path, default_path: Path = DEFAULT_MENU_PATH) -> None:
        self._path = path
        self._default_path = default_path
        self._stamp: int | None = None
        self._menu = CompiledMenu(entries=(), errors=())
        self._default_menu: CompiledMenu | None = None

    @property
    def path(self) -> Path:
        return self._path

    def ensure_user_file(self) -> Path:
        """Create the user file from the default if it does not exist; return its path."""
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self._default_path, self._path)
        return self._path

    def load(self) -> LoadResult:
        """Return the menu, re-reading the file when its modification time changed.

        An invalid file is reported and replaced by the built-in default menu.
        A user file that cannot even be created, stat'd or read (permissions, a
        deleted config directory, invalid encoding, ...) falls back the same way;
        the mtime is not cached in that case, so a later successful read reloads it.
        """
        try:
            stamp, text = self._read_user_file()
        except (OSError, UnicodeDecodeError) as exc:
            message = f"{self._path}: cannot read user menu ({exc}) — using the built-in user menu"
            return LoadResult(menu=self._default_menu_compiled(), messages=(message,))
        if stamp == self._stamp:
            return LoadResult(menu=self._menu, messages=())
        messages: list[str] = []
        try:
            entries = parse_menu(text)
        except MenuConfigError as exc:
            messages.append(f"{self._path}: {exc} — using the built-in user menu")
            entries = parse_menu(self._default_path.read_text())
        self._menu = compile_menu(entries)
        self._stamp = stamp
        return LoadResult(menu=self._menu, messages=(*messages, *self._menu.errors))

    def _read_user_file(self) -> tuple[int, str]:
        """Return the user file's modification time (ns) and text, creating it first if needed."""
        path = self.ensure_user_file()
        return path.stat().st_mtime_ns, path.read_text()

    def _default_menu_compiled(self) -> CompiledMenu:
        """Return the compiled built-in default menu, computed once and cached."""
        if self._default_menu is None:
            self._default_menu = compile_menu(parse_menu(self._default_path.read_text()))
        return self._default_menu
