"""Tests that the Dummy Op development action is gone."""

from __future__ import annotations

from pathlib import Path

from nova_navigator.nova_navigator import MainScreen
from nova_widgets import KeybindingsConfig


def test_stale_dummy_override_is_ignored(tmp_path: Path) -> None:
    """A leftover override for the removed action does not resolve."""
    (tmp_path / "keybindings.toml").write_text('[bindings]\n"app.start_dummy_operation" = "ctrl+d"\n')
    resolved = KeybindingsConfig(tmp_path).resolve(MainScreen.ACTIONS)
    assert "app.start_dummy_operation" not in resolved
