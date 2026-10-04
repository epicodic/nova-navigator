"""The footer of nova_edit shows a key for every feature and the keys work (REQ-16)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen
from nova_widgets.key_types import KeyFormatStyle, KeySequence
from nova_widgets.keymap import HintBar

FEATURES: dict[str, tuple[str, str]] = {
    "save": ("ctrl+s", "Save"),
    "save_as": ("ctrl+shift+s", "Save As"),
    "reload": ("f5", "Reload"),
    "goto": ("ctrl+g", "Go to"),
    "find": ("ctrl+f", "Find"),
    "find_next": ("f3", "Find Next"),
    "find_previous": ("shift+f3", "Find Previous"),
    "toggle_wrap": ("f10", "Wrap Mode"),
    "quit_editor": ("ctrl+q", "Quit"),
}
"""Action name -> (key, label shown in the hint bar); the keys of the original footer with the three intended key changes."""


def test_every_feature_has_a_shown_binding() -> None:
    """The actions that the hint bar shows are exactly the features of the old footer, each with its key."""
    shown = {a.action: (str(a.shortcut).lower(), a.text.rstrip("\u2026")) for a in EditorScreen().ACTIONS if a.show_in_bar}
    assert shown == FEATURES


def test_escape_stays_hidden() -> None:
    """Escape is not a hint bar action; the screen binds it hidden."""
    assert [b.show for b in EditorScreen.BINDINGS if b.key == "escape"] == [False]
    assert all(str(a.shortcut) != "escape" for a in EditorScreen().ACTIONS)


@pytest.mark.asyncio
async def test_the_hint_bar_renders_every_key(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    app = NovaEditApp(path=path)
    async with app.run_test(size=(200, 24)) as pilot:
        await pilot.pause()
        hint_bar = app.query_one(HintBar)
        assert hint_bar.region.width == 200
        text = str(hint_bar.render())
        for key, label in FEATURES.values():
            shown_key = str(KeySequence.parse(key).format(KeyFormatStyle.CLASSIC))
            assert f"{shown_key}  {label}" in text, (shown_key, label)


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "key"), [(action, spec[0]) for action, spec in FEATURES.items()])
async def test_pressing_the_key_runs_the_action(action: str, key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    calls: list[str] = []
    # Patch the EditorScreen method since actions have moved to the screen
    monkeypatch.setattr(EditorScreen, f"action_{action}", lambda _self: calls.append(action))
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
