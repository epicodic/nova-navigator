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

    existing = keys(NovaTextArea.BINDINGS) + [k for k in keys(NovaEditApp.BINDINGS) if k not in ("f4", "ctrl+g")] + keys(App.BINDINGS)
    # Check that f4 and ctrl+g don't collide (escape is already in widget)
    for new in ("f4", "ctrl+g"):
        assert new not in existing, new
    app_keys = keys(NovaEditApp.BINDINGS)
    assert app_keys.count("f4") == 1
    assert app_keys.count("ctrl+g") == 1
