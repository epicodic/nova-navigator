"""Tests for nova_edit bindings and the GotoBar widget."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from textual.app import App
from textual.widgets import Input

from nova_editor import search_bar
from nova_editor.app import GotoTarget, NovaEditApp, parse_goto
from nova_editor.search_bar import SearchBar, SearchStatus
from nova_editor.widget import NovaTextArea


def test_parse_goto() -> None:
    """Test parse_goto function."""
    assert parse_goto("120") == GotoTarget("line", 120)
    assert parse_goto(" @4096 ") == GotoTarget("byte", 4096)
    assert parse_goto("abc") is None
    assert parse_goto("") is None
    assert parse_goto("@-1") is None


def test_new_bindings_do_not_collide() -> None:
    """Test that new bindings do not collide with existing ones.

    Note: escape is handled by the widget (for canceling pending jumps) and is excluded.
    """

    def keys(bindings: Iterable[Any]) -> list[str]:
        out: list[str] = []
        for b in bindings:
            spec = b if isinstance(b, str) else (b[0] if isinstance(b, tuple) else b.key)
            out.extend(k.strip() for k in spec.split(","))
        return out

    new_keys = ("f4", "ctrl+g", "f2", "f5", "f7", "f3", "shift+f3")
    existing = keys(NovaTextArea.BINDINGS) + [k for k in keys(NovaEditApp.BINDINGS) if k not in (*new_keys, "escape")] + keys(App.BINDINGS) + keys(Input.BINDINGS)
    for new in new_keys:
        assert new not in existing, new
    app_keys = keys(NovaEditApp.BINDINGS)
    for new in new_keys:
        assert app_keys.count(new) == 1, new
    # Escape: the widget cancels a pending jump (active only then), the app cancels a running save (active only then)
    assert keys(NovaTextArea.BINDINGS).count("escape") == 1
    assert app_keys.count("escape") == 1
    assert NovaEditApp.check_action is not App.check_action
    # Alt+C toggles the case in the search bar and is used nowhere else
    assert keys(SearchBar.BINDINGS).count("alt+c") == 1
    for owner in (NovaTextArea.BINDINGS, NovaEditApp.BINDINGS, App.BINDINGS, Input.BINDINGS):
        assert "alt+c" not in keys(owner)


def binding_pairs(bindings: Iterable[Any]) -> list[tuple[str, str]]:
    """(key, action) of every key of `bindings`."""
    out: list[tuple[str, str]] = []
    for b in bindings:
        spec, action = (b[0], b[1]) if isinstance(b, tuple) else (b.key, b.action)
        out.extend((k.strip(), action) for k in spec.split(","))
    return out


def test_select_all_moved_off_f7_and_is_still_reachable() -> None:
    """F7 is the search key; the text area selects all with Ctrl+Shift+A or F8."""
    area = binding_pairs(NovaTextArea.BINDINGS)
    assert "f7" not in [key for key, _ in area]
    assert sorted(key for key, action in area if action == "select_all") == ["ctrl+shift+a", "f8"]
    # f8 and ctrl+shift+a have no other meaning in the editor or the app
    others = [key for key, action in area + binding_pairs(NovaEditApp.BINDINGS) + binding_pairs(App.BINDINGS) if action != "select_all"]
    assert "f8" not in others
    assert "ctrl+shift+a" not in others


def test_search_bar_module_surface() -> None:
    """DEC-29 note 2: the module defines exactly `SearchBar` and `SearchStatus`; `SearchStatus` has three public methods."""
    classes = [name for name in dir(search_bar) if not name.startswith("_") and isinstance(getattr(search_bar, name), type) and getattr(search_bar, name).__module__ == search_bar.__name__]
    assert classes == ["SearchBar", "SearchStatus"]
    methods = sorted(name for name, value in vars(SearchStatus).items() if not name.startswith("_") and callable(value))
    assert methods == ["clear", "show_progress", "show_result"]


def test_app_bindings_name_existing_actions() -> None:
    """Every binding action dispatches to an ``action_<name>`` method (no ``action_`` prefix in the binding)."""
    for b in NovaEditApp.BINDINGS:
        action = b.action
        assert not action.startswith("action_"), action
        assert hasattr(NovaEditApp, f"action_{action}"), action
