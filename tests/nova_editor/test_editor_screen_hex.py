"""The hex mode of the editor screen: the action `editor.hex_mode` (F4), View > Hex Mode, focus and flows (S0003 ACT4 task 5, design 7)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.pilot import Pilot

from nova_editor.app import NovaEditApp, build_app
from nova_editor.editor_actions import build_editor_actions
from nova_editor.screen import EditorScreen
from nova_editor.status_line import StatusLine
from nova_editor.widget import HexView, NovaTextArea
from nova_editor.widget.hex_view import ByteSelection
from nova_widgets.key_types import KeySequence
from tests.nova_editor.dialog_helpers import HomeProvider, answer, open_file_dialog, wait_box
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import SETTINGS, Gate, wait_saved
from tests.nova_editor.screen_host import EditorScreenHost, write_keys
from tests.nova_editor.test_keyboard_shortcuts import open_dialog, rows
from tests.nova_editor.test_open_flow import pick
from tests.nova_editor.view_menu import choose_view_item, view_marks

WIDE = (160, 24)
NOTE = "Not available in the hex view yet"
TEXT = "one\ntwo\nthree\n"


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small save chunks and no poll during a test (a test that rewrites the file decides itself when to reload)."""
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)
    monkeypatch.setattr(NovaEditApp, "POLL_SECONDS", 3600.0)


def make_file(tmp_path: Path, text: str = TEXT, name: str = "f.txt") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def app_for(tmp_path: Path, path: Path | None) -> NovaEditApp:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return NovaEditApp(file_path=path, file_provider=HomeProvider(home))


def screen_of(app: NovaEditApp) -> EditorScreen:
    screen = app.screen
    assert isinstance(screen, EditorScreen)
    return screen


async def toggle(pilot: Pilot[None], screen: EditorScreen, key: str = "f4") -> None:
    """Press `key` and wait until the shown widget has the focus."""
    before = screen.document.hex_mode
    await pilot.press(key)
    await wait_until(pilot, lambda: screen.document.hex_mode is not before and screen.focused is screen.document.shown)
    await pilot.pause()


def note_of(app: NovaEditApp) -> str | None:
    return app.query_one(StatusLine).note


# The action


def test_the_hex_mode_action() -> None:
    action = next(a for a in build_editor_actions() if a.id == "editor.hex_mode")
    assert action.text == "Hex Mode"
    assert action.action == "hex_mode"
    assert action.shortcut == KeySequence.parse("f4")
    assert action.checkable
    assert not action.checked


@pytest.mark.asyncio
async def test_the_action_is_in_the_screen_and_the_third_view_item() -> None:
    host = EditorScreenHost(standalone=True)
    async with host.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        assert "editor.hex_mode" in [a.id for a in screen.ACTIONS]
        assert [a.id for a in screen.menu_bar.actions[3].actions][2] == "editor.hex_mode"


@pytest.mark.asyncio
async def test_the_keybindings_dialog_lists_hex_mode_with_f4(tmp_path: Path) -> None:
    app = build_app(["--config-dir", str(tmp_path)])
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        dialog = await open_dialog(pilot, app, screen)
        assert rows(dialog)["Hex Mode"] == "F4"


# Switching


@pytest.mark.asyncio
async def test_f4_switches_both_ways(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        document = screen.document
        assert document.hex_mode is False
        await toggle(pilot, screen)
        assert document.hex_mode is True
        assert screen.focused is document.hex_view
        assert document.hex_view.hex_layout.bytes_per_row == 16  # 80 columns: the REQ-5 row (R2)
        await toggle(pilot, screen)
        assert document.hex_mode is False
        assert screen.focused is document.editor


@pytest.mark.asyncio
async def test_the_embedded_screen_switches_too(tmp_path: Path) -> None:
    host = EditorScreenHost(make_file(tmp_path), standalone=False)
    async with host.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        assert list(screen.query(HexView)) == [screen.document.hex_view]
        assert screen.document.hex_view.display is False
        screen.action_hex_mode()
        await wait_until(pilot, lambda: screen.focused is screen.document.hex_view)
        assert screen.document.hex_view.hex_layout.bytes_per_row == 16
        assert screen._by_id["editor.hex_mode"].checked


@pytest.mark.asyncio
async def test_menu_view_hex_mode_switches_and_the_mark_mirrors(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        assert await view_marks(pilot, screen) == {"Line Numbers": True, "Wrap Mode": False, "Hex Mode": False}
        await choose_view_item(pilot, screen, "Hex Mode")
        await wait_until(pilot, lambda: screen.document.hex_mode and screen.focused is screen.document.hex_view)
        assert await view_marks(pilot, screen) == {"Line Numbers": True, "Wrap Mode": False, "Hex Mode": True}
        await choose_view_item(pilot, screen, "Hex Mode")
        await wait_until(pilot, lambda: not screen.document.hex_mode and screen.focused is screen.document.editor)
        assert await view_marks(pilot, screen) == {"Line Numbers": True, "Wrap Mode": False, "Hex Mode": False}


@pytest.mark.asyncio
async def test_the_item_is_checked_exactly_while_hex_is_shown(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        action = screen._by_id["editor.hex_mode"]
        assert not action.checked
        for expected in (True, False, True, False):
            await toggle(pilot, screen)
            assert action.checked is expected
            assert screen.document.hex_mode is expected


@pytest.mark.asyncio
async def test_rebinding_f4_to_another_key_and_unmapping(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    config = write_keys(tmp_path / "moved", {"editor.hex_mode": KeySequence.parse("ctrl+t")})
    app = NovaEditApp(file_path=path, keybindings=config)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await pilot.press("f4")
        await pilot.pause()
        assert screen.document.hex_mode is False
        await toggle(pilot, screen, "ctrl+t")
        assert screen.document.hex_mode is True
        assert screen._by_id["editor.hex_mode"].shortcut_label == "Ctrl+T"
    config = write_keys(tmp_path / "unmapped", {"editor.hex_mode": None})
    app = NovaEditApp(file_path=path, keybindings=config)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await pilot.press("f4")
        await pilot.pause()
        assert screen.document.hex_mode is False
        assert screen._by_id["editor.hex_mode"].shortcut_label == ""


# The document survives a toggle


@pytest.mark.asyncio
async def test_edits_modified_and_history_survive_a_toggle(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        await pilot.press("x", "y")
        history = editor.document.history
        undo_before = history.undo_stack
        redo_before = history.redo_stack
        await toggle(pilot, screen)
        await toggle(pilot, screen)
        assert editor.text == "xy" + TEXT
        assert editor.modified is True
        assert history.undo_stack == undo_before
        assert history.redo_stack == redo_before
        editor.undo()
        assert editor.text == TEXT


@pytest.mark.asyncio
async def test_toggle_checkpoints(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        history = screen.document.editor.document.history
        with patch.object(history, "checkpoint", wraps=history.checkpoint) as spy:
            await toggle(pilot, screen)
            after_first = spy.call_count
            await toggle(pilot, screen)
        assert after_first >= 1
        assert spy.call_count >= after_first + 1


@pytest.mark.asyncio
async def test_stale_text_view_is_refreshed_after_a_foreign_change(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        hex_view = screen.document.hex_view
        await toggle(pilot, screen)
        document = editor.document
        document.splice_bytes(0, 0, document._table.add(b"NEW "))
        assert editor._stale is True
        await pilot.pause()
        hex_view.refresh()
        await pilot.pause()
        assert hex_view.render_line(0).text.startswith("00000000  4E 45 57 20 6F 6E 65")  # the revision pull draws it at once
        await toggle(pilot, screen)
        assert editor._stale is False
        assert editor.text == "NEW " + TEXT
        assert editor.refresh_if_changed() is False


# Keys inside the hex view


@pytest.mark.asyncio
async def test_tab_and_ctrl_a_act_in_the_hex_view(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        hex_view = screen.document.hex_view
        await toggle(pilot, screen)
        await pilot.press("tab")
        await pilot.pause()
        assert hex_view.cursor.pane == "char"
        assert screen.focused is hex_view
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert hex_view.selection == ByteSelection(0, len(TEXT))
        assert screen.document.editor.selection.is_empty  # the hidden text widget is not touched


@pytest.mark.asyncio
async def test_ctrl_a_moved_key_uses_shown(tmp_path: Path) -> None:
    config = write_keys(tmp_path / "cfg", {"editor.select_all": KeySequence.parse("ctrl+shift+a")})
    app = NovaEditApp(file_path=make_file(tmp_path), keybindings=config)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        hex_view = screen.document.hex_view
        await toggle(pilot, screen)
        await pilot.press("ctrl+a")  # the old key of a moved editing action is swallowed for the shown widget
        await pilot.pause()
        assert hex_view.selection is None
        await pilot.press("ctrl+shift+a")
        await pilot.pause()
        assert hex_view.selection == ByteSelection(0, len(TEXT))


# Focus after flows (R6)


@pytest.mark.asyncio
async def test_focus_returns_to_the_shown_widget_after_open_save_as_and_popups(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        hex_view = screen.document.hex_view
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: screen.find_popup.display)
        await toggle(pilot, screen)  # F4 from the popup hides it and shows the hex view
        assert not screen.find_popup.display
        assert screen.focused is hex_view
        for key in ("ctrl+o", "ctrl+shift+s"):
            await pilot.press(key)
            await wait_until(pilot, lambda: open_file_dialog(app) is not None)
            await pilot.press("escape")
            await wait_until(pilot, lambda: open_file_dialog(app) is None and screen.focused is hex_view)
        await toggle(pilot, screen)
        await pilot.press("x")
        await toggle(pilot, screen)
        await pilot.press("ctrl+q")
        await wait_box(pilot, app)
        await answer(pilot, app, "Cancel")
        await wait_until(pilot, lambda: app.screen is screen and screen.focused is hex_view)


# Save, reload, open


@pytest.mark.asyncio
async def test_save_in_hex_mode_writes_the_file_and_clears_modified(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        await pilot.press("x")  # an insertion: the streaming save
        await toggle(pilot, screen)
        await pilot.press("ctrl+s")
        await wait_until(pilot, lambda: path.read_text() == "x" + TEXT)
        await wait_saved(pilot, editor)
        assert not editor.modified
        assert screen.document.hex_mode is True


@pytest.mark.asyncio
async def test_save_in_place_in_hex_mode(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    inode = os.stat(path).st_ino
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        await pilot.press("shift+right", "x")  # an overwrite of the same length: the in-place save
        await toggle(pilot, screen)
        await pilot.press("ctrl+s")
        await wait_until(pilot, lambda: path.read_text() == "xne\ntwo\nthree\n")
        await wait_saved(pilot, editor)
        assert os.stat(path).st_ino == inode
        assert not editor.modified


@pytest.mark.asyncio
async def test_reload_in_hex_mode_repaints_at_once(tmp_path: Path) -> None:
    path = make_file(tmp_path, "0123456789" * 6 + "\n")
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        hex_view = screen.document.hex_view
        await toggle(pilot, screen)
        hex_view.set_cursor(55)
        old = screen.document.editor.document
        path.write_bytes(b"short\n")
        await pilot.press("f5")
        await wait_until(pilot, lambda: screen.document.editor.document is not old and note_of(app) == "Reloaded")
        await pilot.pause()
        assert hex_view.length == 6
        assert hex_view.cursor.offset == 6  # clamped to the end of the shorter file (R12)
        assert hex_view.render_line(0).text.startswith("00000000  73 68 6F 72 74 0A")
        assert screen.document.hex_mode is True


@pytest.mark.asyncio
async def test_open_replaces_both_widgets_and_starts_in_text_mode(tmp_path: Path) -> None:
    first = make_file(tmp_path, "one\n", "first.txt")
    make_file(tmp_path, "two\n", "second.txt")
    app = app_for(tmp_path, first)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        old = screen.document
        await toggle(pilot, screen)
        await pilot.press("ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pick(pilot, tmp_path, "second.txt")
        await wait_until(pilot, lambda: screen.document is not old and screen.document.editor.text == "two\n")
        await pilot.pause()
        document = screen.document
        assert not old.editor.is_attached
        assert not old.hex_view.is_attached
        assert document.hex_mode is False
        assert screen.focused is document.editor
        assert not screen._by_id["editor.hex_mode"].checked
        children = list(screen.children)
        start = children.index(screen.menu_bar)
        assert children[start + 1 : start + 3] == [document.editor, document.hex_view]
        assert list(screen.query(HexView)) == [document.hex_view]


@pytest.mark.asyncio
async def test_the_hex_action_is_ignored_while_the_document_closes(tmp_path: Path) -> None:
    host = EditorScreenHost(make_file(tmp_path), standalone=True)
    async with host.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        document = screen.document
        await document.hex_view.remove()
        await document.editor.remove()  # the text widget closes the document when it is removed
        screen.action_hex_mode()
        await pilot.pause()
        assert document.hex_mode is False


@pytest.mark.asyncio
async def test_toggle_while_a_save_runs_only_swaps_displays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_file(tmp_path, "a" * 100 + "\n")
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    app = NovaEditApp(file_path=path)
    try:
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            screen = screen_of(app)
            editor = screen.document.editor
            await pilot.press("x", "ctrl+s")
            await wait_until(pilot, gate.reached.is_set)
            await toggle(pilot, screen)
            assert editor.saving
            await toggle(pilot, screen)
            assert editor.saving
            gate.release()
            await wait_saved(pilot, editor)
            assert path.read_text() == "x" + "a" * 100 + "\n"
            assert not editor.modified
    finally:
        gate.release()


# Popups in the text mode


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "popup"), [("ctrl+f", "find_popup"), ("ctrl+g", "goto_popup")])
async def test_find_and_goto_still_open_their_popups_in_text_mode(tmp_path: Path, key: str, popup: str) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        await toggle(pilot, screen)
        await pilot.press(key)
        await wait_until(pilot, lambda: getattr(screen, popup).display)
        assert note_of(app) != NOTE


@pytest.mark.asyncio
async def test_line_numbers_and_wrap_do_nothing_in_hex_mode(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        await toggle(pilot, screen)
        wrap, numbers = editor.soft_wrap, editor.show_line_numbers
        await pilot.press("f10", "f11")
        await pilot.pause()
        assert editor.soft_wrap is wrap
        assert editor.show_line_numbers is numbers
        assert screen.document.hex_mode is True
        assert screen.focused is screen.document.hex_view


@pytest.mark.asyncio
async def test_nova_edit_exposes_hex_view(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        assert app.hex_view is screen.document.hex_view
        assert app.editor is screen.document.editor


@pytest.mark.asyncio
async def test_two_hex_mode_actions_before_a_refresh_keep_the_hex_cursor(tmp_path: Path) -> None:
    """Two F4 handled in one batch (key repeat): the second switch must not read the text cursor the first one has not placed yet."""
    app = NovaEditApp(file_path=make_file(tmp_path, "".join(f"row {n:04d}\n" for n in range(400))))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        document = screen.document
        await toggle(pilot, screen)
        document.hex_view.set_cursor(1000)
        screen.action_hex_mode()
        screen.action_hex_mode()
        await wait_until(pilot, lambda: document.hex_mode and screen.focused is document.hex_view)
        await pilot.pause()
        assert document.hex_view.cursor.offset == 1000
        await toggle(pilot, screen)
        await wait_until(pilot, lambda: document.editor.pending_progress is None)
        assert document.editor.cursor_byte_offset == 1000


@pytest.mark.asyncio
async def test_two_hex_mode_actions_with_an_unknown_text_cursor_keep_the_text_cursor(tmp_path: Path) -> None:
    """F4 twice in one batch while the text cursor has no known byte offset: the pending switch carries "unknown", not the stale hex cursor.

    The hex view never showed a position, so the way back must not move the text cursor to the old hex offset.
    """
    app = NovaEditApp(file_path=make_file(tmp_path, "".join(f"row {n:04d}\n" for n in range(400))))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        document = screen.document
        editor = document.editor
        await toggle(pilot, screen)
        document.hex_view.set_cursor(1000)
        await toggle(pilot, screen)
        await wait_until(pilot, lambda: editor.pending_progress is None)
        editor.move_cursor((5, 2))
        await pilot.pause()
        with patch.object(NovaTextArea, "cursor_byte_offset", new=property(lambda _self: None)):
            screen.action_hex_mode()
            screen.action_hex_mode()
            await wait_until(pilot, lambda: not document.hex_mode and screen.focused is editor)
            await pilot.pause()
        assert editor.cursor_location == (5, 2)
        assert document.hex_view.cursor.offset == 1000
        await toggle(pilot, screen)
        assert document.hex_view.cursor.offset == 5 * 9 + 2  # the next switch reads the real cursor again


@pytest.mark.asyncio
async def test_the_hex_action_is_ignored_while_only_the_hex_view_is_removed(tmp_path: Path) -> None:
    """`_swap_document` removes the hex view first: an F4 between the two removals must not show a detached hex view."""
    host = EditorScreenHost(make_file(tmp_path), standalone=True)
    async with host.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        document = screen.document
        await document.hex_view.remove()
        screen.action_hex_mode()
        await pilot.pause()
        assert document.hex_mode is False
        assert document.editor.display is True


@pytest.mark.asyncio
async def test_a_hex_edit_and_the_insert_key_refresh_the_status_line(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = screen_of(app)
        await wait_until(pilot, lambda: screen.focused is screen.document.shown)
        await toggle(pilot, screen)
        with patch.object(StatusLine, "request") as request:
            await pilot.press("1")
            await pilot.pause()
            assert request.call_count >= 1
            request.reset_mock()
            await pilot.press("insert")
            await pilot.pause()
            assert request.call_count >= 1
        assert screen.document.hex_view.insert_mode is True
        assert screen.document.editor.document.read_bytes(0, 3) == bytes([0x1F, 0x6E, 0x65])
