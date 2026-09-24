from __future__ import annotations

import os
from pathlib import Path

from nova_navigator.config import app_config_dir
from nova_navigator.usermenu import DEFAULT_MENU_PATH, UserMenuStore, compile_menu, parse_menu


def _bump_mtime(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_app_config_dir_is_nova_navigator_dir() -> None:
    assert app_config_dir().name == "nova-navigator"


def test_default_menu_parses_and_compiles_cleanly() -> None:
    menu = compile_menu(parse_menu(DEFAULT_MENU_PATH.read_text()))
    assert menu.errors == ()
    assert [c.entry.id for c in menu.entries] == ["extract", "compress", "sha256", "make", "git_log"]


def test_load_creates_user_file_from_default(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "usermenu.toml"
    result = UserMenuStore(path).load()
    assert path.read_text() == DEFAULT_MENU_PATH.read_text()
    assert result.messages == ()
    assert len(result.menu.entries) == 5


def test_load_is_cached_until_mtime_changes(tmp_path: Path) -> None:
    path = tmp_path / "usermenu.toml"
    path.write_text('[a]\nlabel = "A"\nrun = "a"\n')
    store = UserMenuStore(path)
    first = store.load()
    assert store.load().menu is first.menu
    path.write_text('[b]\nlabel = "B"\nrun = "b"\n')
    _bump_mtime(path)
    assert [c.entry.id for c in store.load().menu.entries] == ["b"]


def test_invalid_file_falls_back_to_default_with_message(tmp_path: Path) -> None:
    path = tmp_path / "usermenu.toml"
    path.write_text("[broken\n")
    result = UserMenuStore(path).load()
    assert len(result.menu.entries) == 5
    assert len(result.messages) == 1
    assert "built-in user menu" in result.messages[0]


def test_expression_errors_are_reported_once(tmp_path: Path) -> None:
    path = tmp_path / "usermenu.toml"
    path.write_text('[a]\nlabel = "A"\nrun = "a"\nwhen = "x and"\n')
    store = UserMenuStore(path)
    assert len(store.load().messages) == 1
    assert store.load().messages == ()
