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
        """
        path = self.ensure_user_file()
        stamp = path.stat().st_mtime_ns
        if stamp == self._stamp:
            return LoadResult(menu=self._menu, messages=())
        messages: list[str] = []
        try:
            entries = parse_menu(path.read_text())
        except MenuConfigError as exc:
            messages.append(f"{path}: {exc} — using the built-in user menu")
            entries = parse_menu(self._default_path.read_text())
        self._menu = compile_menu(entries)
        self._stamp = stamp
        return LoadResult(menu=self._menu, messages=(*messages, *self._menu.errors))
