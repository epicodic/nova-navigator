"""The line number gutter and its View menu toggle (REQ-11, REQ-12) and the wrap check mark that sits beside it (REQ-10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen
from nova_editor.status_line import StatusLine
from nova_editor.widget import NovaTextArea
from nova_widgets.keybindings_config import KeybindingsConfig
from tests.nova_editor.helpers_view import GatedRowEditor, wait_until
from tests.nova_editor.view_menu import NARROW, choose_view_item, view_marks


def make_file(tmp_path: Path) -> Path:
    path = tmp_path / "f.txt"
    path.write_bytes(b"one\ntwo\nthree\n")
    return path


def editor_screen(app: NovaEditApp) -> EditorScreen:
    screen = app.screen
    assert isinstance(screen, EditorScreen)
    return screen


def first_row(editor: NovaTextArea) -> str:
    return "".join(segment.text for segment in editor.render_line(0))


def test_the_reusable_widget_still_starts_without_a_gutter() -> None:
    """The widget default is unchanged (S0001 tests and other users rely on it); only the editor screen turns the gutter on."""
    assert NovaTextArea().show_line_numbers is False


@pytest.mark.asyncio
async def test_the_editor_starts_with_the_gutter_and_a_checked_menu_item(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        assert app.editor.show_line_numbers is True
        assert app.editor.gutter_width > 0
        assert first_row(app.editor).lstrip().startswith("1  one")
        drawn = await view_marks(pilot, editor_screen(app))
        assert drawn["Line Numbers"] is True


@pytest.mark.asyncio
async def test_a_buffer_without_a_file_also_starts_with_the_gutter() -> None:
    app = NovaEditApp()
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        assert app.editor.show_line_numbers is True
        drawn = await view_marks(pilot, editor_screen(app))
        assert drawn["Line Numbers"] is True


@pytest.mark.asyncio
async def test_f11_hides_and_shows_the_gutter_at_once_and_the_menu_agrees(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        await pilot.press("f11")
        await pilot.pause()
        assert app.editor.show_line_numbers is False
        assert app.editor.gutter_width == 0
        assert first_row(app.editor).startswith("one")
        drawn = await view_marks(pilot, editor_screen(app))
        assert drawn["Line Numbers"] is False
        await pilot.press("f11")
        await pilot.pause()
        assert first_row(app.editor).lstrip().startswith("1  one")
        drawn = await view_marks(pilot, editor_screen(app))
        assert drawn["Line Numbers"] is True


@pytest.mark.asyncio
async def test_the_view_menu_item_toggles_the_gutter_and_its_own_check_mark(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = editor_screen(app)
        await choose_view_item(pilot, screen, "Line Numbers")
        assert app.editor.show_line_numbers is False
        assert first_row(app.editor).startswith("one")
        drawn = await view_marks(pilot, screen)
        assert drawn["Line Numbers"] is False
        await choose_view_item(pilot, screen, "Line Numbers")
        assert app.editor.show_line_numbers is True
        drawn = await view_marks(pilot, screen)
        assert drawn["Line Numbers"] is True


@pytest.mark.asyncio
async def test_the_toggle_is_for_the_session_only(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    first = NovaEditApp(file_path=path)
    async with first.run_test(size=NARROW) as pilot:
        await pilot.pause()
        await pilot.press("f11")
        await pilot.pause()
        assert first.editor.show_line_numbers is False
    second = NovaEditApp(file_path=path)
    async with second.run_test(size=NARROW) as pilot:
        await pilot.pause()
        assert second.editor.show_line_numbers is True
        drawn = await view_marks(pilot, editor_screen(second))
        assert drawn["Line Numbers"] is True


@pytest.mark.asyncio
async def test_toggling_writes_no_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    config_dir = tmp_path / "keys"
    path = make_file(tmp_path)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    app = NovaEditApp(file_path=path, keybindings=KeybindingsConfig(config_dir))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        await pilot.press("f11", "f10", "f11", "f10")
        await choose_view_item(pilot, editor_screen(app), "Line Numbers")
        await choose_view_item(pilot, editor_screen(app), "Wrap Mode")
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert after == before  # nothing was created: not in the key config dir, not in the fake home
    assert not config_dir.exists()
    assert list(home.iterdir()) == []


def status_parts(app: NovaEditApp) -> list[str]:
    return app.query_one(StatusLine).text.split("  ")


@pytest.mark.asyncio
@pytest.mark.parametrize("start_wrap", [False, True])
async def test_the_wrap_indicator_and_the_menu_mark_agree_after_every_toggle(tmp_path: Path, start_wrap: bool) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path), soft_wrap=start_wrap)
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = editor_screen(app)
        wrap = start_wrap
        for toggle in ("key", "menu", "key"):
            shown = "Wrap" if wrap else "No wrap"
            await wait_until(pilot, lambda shown=shown: shown in status_parts(app))
            assert app.editor.soft_wrap is wrap
            drawn = await view_marks(pilot, screen)
            assert drawn["Wrap Mode"] is wrap
            if toggle == "key":
                await pilot.press("f10")
            else:
                await choose_view_item(pilot, screen, "Wrap Mode")
            wrap = not wrap
        shown = "Wrap" if wrap else "No wrap"
        await wait_until(pilot, lambda: shown in status_parts(app))
        drawn = await view_marks(pilot, screen)
        assert drawn["Wrap Mode"] is wrap


@pytest.mark.asyncio
async def test_the_gutter_toggle_before_the_first_row_is_indexed_does_not_end_the_app(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    path.write_bytes(b"x" * 5000)  # one row; the scan is held back at byte 0, so row 0 is not resolved
    GatedRowEditor.gates.clear()
    app = NovaEditApp(file_path=path, editor_class=GatedRowEditor)
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        gate = GatedRowEditor.gates[0]
        await wait_until(pilot, gate.blocked.is_set)
        await pilot.press("f11")
        await pilot.pause()
        assert app.return_code is None
        assert app.editor.show_line_numbers is False
        await pilot.press("f11")
        await pilot.pause()
        assert app.return_code is None
        assert app.editor.show_line_numbers is True
        gate.release()
        await wait_until(pilot, lambda: app.editor.line_count_exact)
        await wait_until(pilot, lambda: first_row(app.editor).lstrip().startswith("1  xxx"))
