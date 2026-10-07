"""Tests for nova_edit bindings."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from textual.app import App
from textual.screen import Screen
from textual.widgets import Input

from nova_editor import find_popup
from nova_editor.find_popup import FindPopup
from nova_editor.screen import EditorScreen
from nova_editor.widget import NovaTextArea


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

    screen = EditorScreen()
    screen_keys = {a.id: a.initial_shortcut for a in screen.ACTIONS}
    new_ids = ("editor.wrap_mode", "editor.goto", "editor.save_as", "editor.reload", "editor.find", "editor.find_next", "editor.find_previous")
    new_keys = [str(screen_keys[action_id]).lower() for action_id in new_ids]
    assert new_keys == ["f10", "ctrl+g", "ctrl+shift+s", "f5", "ctrl+f", "f3", "shift+f3"]
    existing = keys(NovaTextArea.BINDINGS) + keys(App.BINDINGS) + keys(Input.BINDINGS)
    for new in new_keys:
        assert new not in existing, new
    # every key of the screen belongs to exactly one action
    shortcuts = [str(shortcut) for shortcut in screen_keys.values() if shortcut is not None]
    assert len(shortcuts) == len(set(shortcuts))
    # Escape: the widget cancels a pending jump (active only then), the screen cancels a running save (active only then) or closes an embedded editor
    screen_binding_keys = keys(EditorScreen.BINDINGS)
    assert keys(NovaTextArea.BINDINGS).count("escape") == 1
    assert screen_binding_keys.count("escape") == 2  # cancel_save and close_embedded, each active only in its own case
    assert EditorScreen.check_action is not Screen.check_action
    # Alt+C toggles the case in the Find popup and is used nowhere else
    assert keys(FindPopup.BINDINGS).count("alt+c") == 1
    for owner in (NovaTextArea.BINDINGS, EditorScreen.BINDINGS, App.BINDINGS, Input.BINDINGS):
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
    others = [key for key, action in area + binding_pairs(EditorScreen.BINDINGS) + binding_pairs(App.BINDINGS) if action != "select_all"]
    assert "f8" not in others
    assert "ctrl+shift+a" not in others


def test_find_popup_module_surface() -> None:
    """The module defines exactly `FindPopup`, with its two messages."""
    classes = [name for name in dir(find_popup) if not name.startswith("_") and isinstance(getattr(find_popup, name), type) and getattr(find_popup, name).__module__ == find_popup.__name__]
    assert classes == ["FindPopup"]
    messages = sorted(name for name, value in vars(FindPopup).items() if isinstance(value, type))
    assert messages == ["Dismissed", "Requested"]


def test_every_action_name_has_a_handler() -> None:
    """Every `Action.action` of the screen dispatches to an `action_<name>` method of the screen or of the editor widget."""
    for action in EditorScreen().ACTIONS:
        name = action.action
        assert name is not None
        assert not name.startswith("action_"), name
        assert hasattr(EditorScreen, f"action_{name}") or hasattr(NovaTextArea, f"action_{name}"), name
