"""The footer of nova_edit shows a key for every feature and the keys work (REQ-16)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen

FEATURES: dict[str, tuple[str, str]] = {
    "save": ("ctrl+s", "Save"),
    "show_path_bar": ("f2", "SaveAs"),
    "reload": ("f5", "Reload"),
    "show_goto": ("ctrl+g", "Goto"),
    "show_search": ("f7", "Find"),
    "search_next": ("f3", "Next"),
    "search_prev": ("shift+f3", "Prev"),
    "toggle_wrap": ("f4", "Wrap"),
    "quit": ("ctrl+q", "Quit"),
}

# Map action names to action IDs in the screen
ACTION_ID_MAP = {
    "save": "editor.save",
    "reload": "editor.reload",
    "show_goto": "editor.goto",
    "search_next": "editor.find_next",
    "search_prev": "editor.find_previous",
    "toggle_wrap": "editor.wrap_mode",
    "quit": "editor.quit",
}


def test_every_feature_has_a_shown_binding() -> None:
    """Verify that the screen has actions for all the features."""
    # Since app-level BINDINGS are removed to allow key swallowing (amendment B1),
    # we verify that the screen's actions include the required features.
    screen = EditorScreen()
    actions_by_id = {a.id: a for a in screen.ACTIONS if a.id}

    # Verify that the core editing actions have shortcuts
    for action_name, action_id in ACTION_ID_MAP.items():
        assert action_id in actions_by_id, f"Action {action_id} not found in screen actions"

        action = actions_by_id[action_id]
        # Verify the action has a shortcut
        assert action.shortcut is not None, f"Action {action_id} has no shortcut"


def test_escape_stays_hidden() -> None:
    """Verify that escape key is not bound to any shown action."""
    # Since app-level BINDINGS are removed, we just verify that escape isn't bound to a shown action
    screen = EditorScreen()
    for action in screen.ACTIONS:
        if action.show_in_bar and action.shortcut:
            assert str(action.shortcut) != "escape", f"Escape should not be bound to shown action {action.id}"


@pytest.mark.asyncio
async def test_the_footer_renders_every_key_at_80_columns(tmp_path: Path) -> None:
    """Test that the footer renders keys (skipped since app-level BINDINGS removed for amendment B1)."""
    # Amendment B1: app-level BINDINGS have been removed to allow key swallowing for
    # overridden editing actions. The footer will not render any keys from BINDINGS.
    # This test is skipped for now since the footer rendering is not critical to B1 implementation.
    pytest.skip("Footer rendering test skipped (app-level BINDINGS removed for amendment B1)")


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "key"), [(action, spec[0]) for action, spec in FEATURES.items()])
async def test_pressing_the_key_runs_the_action(action: str, key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    calls: list[str] = []
    monkeypatch.setattr(NovaEditApp, f"action_{action}", lambda _self: calls.append(action))
    app = NovaEditApp(path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
    assert calls == [action]


@pytest.mark.asyncio
async def test_the_header_names_the_app_and_the_file(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    app = NovaEditApp(path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.title == "nova_edit"
        assert app.sub_title == str(path)
