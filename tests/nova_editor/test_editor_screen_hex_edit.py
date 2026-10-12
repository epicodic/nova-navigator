"""The six editing actions act on the shown view in hex mode: by menu and by key, rebound keys, read-only (S0003 ACT5 commit 5, task 9)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.pilot import Pilot

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen
from nova_editor.widget import NovaTextArea
from nova_widgets.key_types import KeySequence
from tests.nova_editor.save_widget_helpers import SETTINGS
from tests.nova_editor.screen_host import write_keys
from tests.nova_editor.test_editor_screen_hex import app_for, make_file, screen_of, toggle
from tests.nova_editor.view_menu import choose_item

WIDE = (160, 24)
EDIT_MENU_INDEX = 1
LABELS = ["Undo", "Redo", "Cut", "Copy", "Paste", "Select All"]


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)
    monkeypatch.setattr(NovaEditApp, "POLL_SECONDS", 3600.0)


def data_of(screen: EditorScreen) -> bytes:
    document = screen.document.editor.document
    return document.read_bytes(0, document.length)


async def edit_item(pilot: Pilot[None], screen: EditorScreen, label: str) -> None:
    await choose_item(pilot, screen, EDIT_MENU_INDEX, label)
    await pilot.pause()


@pytest.mark.asyncio
async def test_the_edit_menu_acts_on_the_hex_view(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path, "abcdef"))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        view = screen.document.hex_view
        await edit_item(pilot, screen, "Select All")
        assert view.selection is not None
        assert (view.selection.start, view.selection.end) == (0, 6)
        view.set_cursor(0)
        view.set_cursor(2, select=True)
        await edit_item(pilot, screen, "Copy")
        assert app.clipboard == "61 62"
        await edit_item(pilot, screen, "Cut")
        assert data_of(screen) == b"cdef"
        view.set_cursor(4)
        await edit_item(pilot, screen, "Paste")
        assert data_of(screen) == b"cdefab"
        await edit_item(pilot, screen, "Undo")
        assert data_of(screen) == b"cdef"
        await edit_item(pilot, screen, "Redo")
        assert data_of(screen) == b"cdefab"


@pytest.mark.asyncio
async def test_the_keys_act_on_the_hex_view(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path, "abcdef"))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        view = screen.document.hex_view
        await pilot.press("ctrl+a")
        assert view.selection is not None
        view.set_cursor(0)
        view.set_cursor(2, select=True)
        await pilot.press("ctrl+c")
        assert app.clipboard == "61 62"
        await pilot.press("ctrl+x")
        assert data_of(screen) == b"cdef"
        view.set_cursor(4)
        await pilot.press("ctrl+v")
        assert data_of(screen) == b"cdefab"
        await pilot.press("ctrl+z")
        assert data_of(screen) == b"cdef"
        await pilot.press("ctrl+y")
        assert data_of(screen) == b"cdefab"


@pytest.mark.asyncio
async def test_a_rebound_key_works_in_the_hex_view(tmp_path: Path) -> None:
    config = write_keys(tmp_path / "keys", {"editor.copy": KeySequence.parse("ctrl+k")})
    app = NovaEditApp(file_path=make_file(tmp_path, "abcdef"), keybindings=config)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        view = screen.document.hex_view
        view.set_cursor(0)
        view.set_cursor(3, select=True)
        await pilot.press("ctrl+k")
        assert app.clipboard == "61 62 63"


@pytest.mark.asyncio
async def test_a_read_only_document_refuses_hex_edits_but_copies(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path, "abcdef"))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        screen.document.editor.read_only = True
        await toggle(pilot, screen)
        view = screen.document.hex_view
        view.set_cursor(0)
        view.set_cursor(2, select=True)
        await pilot.press("ctrl+c")
        assert app.clipboard == "61 62"
        await pilot.press("ctrl+x", "ctrl+v", "delete")
        assert data_of(screen) == b"abcdef"
        assert screen.document.editor.modified is False
