"""The footer of nova_edit shows a key for every feature and the keys work (REQ-16)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.binding import Binding
from textual.widgets import Footer

from nova_editor.app import NovaEditApp

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


def test_every_feature_has_a_shown_binding() -> None:
    shown = {b.action: (b.key, b.description) for b in NovaEditApp.BINDINGS if isinstance(b, Binding) and b.show}
    assert shown == FEATURES


def test_escape_stays_hidden() -> None:
    hidden = [b for b in NovaEditApp.BINDINGS if isinstance(b, Binding) and b.key == "escape"]
    assert [b.show for b in hidden] == [False]


@pytest.mark.asyncio
async def test_the_footer_renders_every_key_at_80_columns(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert len(app.query(Footer)) == 1
        keys = list(app.query("FooterKey"))
        assert len(keys) == len(FEATURES)
        text = " ".join(str(key.render()) for key in keys)
        for _key, description in FEATURES.values():
            assert description in text, description
        assert all(key.region.width > 0 and key.region.right <= 80 for key in keys)


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "key"), [(action, spec[0]) for action, spec in FEATURES.items()])
async def test_pressing_the_key_runs_the_action(action: str, key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    calls: list[str] = []
    monkeypatch.setattr(NovaEditApp, f"action_{action}", lambda _self: calls.append(action))
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
    assert calls == [action]


@pytest.mark.asyncio
async def test_the_header_names_the_app_and_the_file(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.title == "nova_edit"
        assert app.sub_title == str(path)
