"""Tests for nova_edit bindings and the GotoBar widget."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from textual.app import App

from nova_editor.app import GotoTarget, NovaEditApp, parse_goto
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

    new_keys = ("f4", "ctrl+g", "f2", "f5")
    existing = keys(NovaTextArea.BINDINGS) + [k for k in keys(NovaEditApp.BINDINGS) if k not in (*new_keys, "escape")] + keys(App.BINDINGS)
    for new in new_keys:
        assert new not in existing, new
    app_keys = keys(NovaEditApp.BINDINGS)
    for new in new_keys:
        assert app_keys.count(new) == 1, new
    # Escape: the widget cancels a pending jump (active only then), the app cancels a running save (active only then)
    assert keys(NovaTextArea.BINDINGS).count("escape") == 1
    assert app_keys.count("escape") == 1
    assert NovaEditApp.check_action is not App.check_action


def test_app_bindings_name_existing_actions() -> None:
    """Every binding action dispatches to an ``action_<name>`` method (no ``action_`` prefix in the binding)."""
    for b in NovaEditApp.BINDINGS:
        action = b[1]
        assert not action.startswith("action_"), action
        assert hasattr(NovaEditApp, f"action_{action}"), action
