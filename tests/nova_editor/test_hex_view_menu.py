"""The View menu while the hex view is shown (REQ-20): Line Numbers and Wrap Mode are disabled, Hex Mode stays usable."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
from textual.geometry import Region
from textual.pilot import Pilot

from nova_editor.app import NovaEditApp
from nova_editor.document_view import DocumentView
from nova_editor.screen import EditorScreen
from nova_editor.status_line import StatusLine
from nova_editor.widget import NovaTextArea
from nova_widgets.key_types import KeySequence
from nova_widgets.menu import Menu
from tests.nova_editor.dialog_helpers import HomeProvider, open_file_dialog
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import SETTINGS
from tests.nova_editor.screen_host import write_keys
from tests.nova_editor.test_open_flow import pick
from tests.nova_editor.view_menu import NARROW, open_view_menu, view_marks

IDS = ("editor.line_numbers", "editor.wrap_mode")


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small save chunks and no poll during a test."""
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)
    monkeypatch.setattr(NovaEditApp, "POLL_SECONDS", 3600.0)


def make_text(tmp_path: Path, name: str = "f.txt") -> Path:
    path = tmp_path / name
    path.write_text("one\ntwo\nthree\n")
    return path


def make_binary(tmp_path: Path, name: str = "b.dat") -> Path:
    path = tmp_path / name
    path.write_bytes(b"ab\x00cd" * 10)
    return path


def screen_of(app: NovaEditApp) -> EditorScreen:
    screen = app.screen
    assert isinstance(screen, EditorScreen)
    return screen


def enabled_of(screen: EditorScreen) -> dict[str, bool]:
    return {item: screen._by_id[item].enabled for item in (*IDS, "editor.hex_mode")}


async def toggle(pilot: Pilot[None], screen: EditorScreen, key: str = "f4") -> None:
    before = screen.document.hex_mode
    await pilot.press(key)
    await wait_until(pilot, lambda: screen.document.hex_mode is not before and screen.focused is screen.document.shown)
    await pilot.pause()


def row_of(menu: Menu, label: str) -> int:
    """The line (border included) on which the menu draws `label`."""
    return [action.text for action in menu.actions].index(label) + 1


def is_drawn_disabled(menu: Menu, label: str) -> bool:
    disabled = menu.get_component_rich_style("disabled")
    return any(segment.style is not None and segment.style.color == disabled.color for segment in menu.render_line(row_of(menu, label)))


def highlighted_text(menu: Menu) -> str:
    """The label of the highlighted item (the menu must have one)."""
    index = menu._highlighted
    assert index is not None
    return menu.actions[index].text


async def close(pilot: Pilot[None], screen: EditorScreen, menu: Menu) -> None:
    await pilot.press("escape")
    await wait_until(pilot, lambda: menu not in list(screen.query(Menu)))


@pytest.mark.asyncio
async def test_the_two_items_are_disabled_in_hex_and_enabled_in_text(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_text(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        assert enabled_of(screen) == {"editor.line_numbers": True, "editor.wrap_mode": True, "editor.hex_mode": True}
        await toggle(pilot, screen)
        assert enabled_of(screen) == {"editor.line_numbers": False, "editor.wrap_mode": False, "editor.hex_mode": True}
        await toggle(pilot, screen)
        assert enabled_of(screen) == {"editor.line_numbers": True, "editor.wrap_mode": True, "editor.hex_mode": True}


@pytest.mark.asyncio
async def test_the_hex_mode_item_stays_enabled(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_binary(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        assert screen.document.hex_mode
        assert screen._by_id["editor.hex_mode"].enabled
        await toggle(pilot, screen)
        assert screen._by_id["editor.hex_mode"].enabled


@pytest.mark.asyncio
async def test_disabled_items_are_drawn_disabled_and_keep_their_check_state(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_text(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        text_marks = await view_marks(pilot, screen)
        await toggle(pilot, screen)
        menu = await open_view_menu(pilot, screen)
        assert is_drawn_disabled(menu, "Line Numbers")
        assert is_drawn_disabled(menu, "Wrap Mode")
        assert not is_drawn_disabled(menu, "Hex Mode")
        await close(pilot, screen, menu)
        marks = await view_marks(pilot, screen)
        assert marks["Line Numbers"] is text_marks["Line Numbers"] is editor.show_line_numbers
        assert marks["Wrap Mode"] is text_marks["Wrap Mode"] is editor.soft_wrap
        assert marks["Hex Mode"] is True


@pytest.mark.asyncio
async def test_arrow_keys_skip_the_disabled_items(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_binary(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        menu = await open_view_menu(pilot, screen)
        await pilot.press("down")
        assert highlighted_text(menu) == "Hex Mode"
        await pilot.press("down")
        assert highlighted_text(menu) == "Hex Mode"
        await pilot.press("up")
        assert highlighted_text(menu) == "Hex Mode"


@pytest.mark.asyncio
async def test_a_click_on_a_disabled_item_changes_nothing(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_binary(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        wrap, numbers = editor.soft_wrap, editor.show_line_numbers
        before = await view_marks(pilot, screen)
        for label in ("Line Numbers", "Wrap Mode"):
            menu = await open_view_menu(pilot, screen)
            await pilot.hover(menu, offset=(3, row_of(menu, label)))
            await pilot.click(menu, offset=(3, row_of(menu, label)))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            if menu in list(screen.query(Menu)):
                await close(pilot, screen, menu)
        assert editor.soft_wrap is wrap
        assert editor.show_line_numbers is numbers
        assert screen.document.hex_mode
        assert await view_marks(pilot, screen) == before


@pytest.mark.asyncio
async def test_f10_and_f11_do_nothing_in_hex(tmp_path: Path) -> None:
    path = make_text(tmp_path)
    moved = write_keys(tmp_path / "keys", {"editor.wrap_mode": KeySequence.parse("ctrl+w"), "editor.line_numbers": KeySequence.parse("ctrl+l")})
    for keys, config in ((("f10", "f11"), None), (("ctrl+w", "ctrl+l"), moved)):
        app = NovaEditApp(file_path=path, keybindings=config)
        async with app.run_test(size=NARROW) as pilot:
            await pilot.pause()
            screen = screen_of(app)
            editor = screen.document.editor
            await toggle(pilot, screen)
            wrap, numbers = editor.soft_wrap, editor.show_line_numbers
            await pilot.press(*keys)
            await pilot.pause()
            assert editor.soft_wrap is wrap
            assert editor.show_line_numbers is numbers
            assert screen.document.hex_mode
            assert screen.focused is screen.document.hex_view
            assert not app.query_one(StatusLine).note


@pytest.mark.asyncio
async def test_the_actions_guard_themselves(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_text(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        await toggle(pilot, screen)
        wrap, numbers = editor.soft_wrap, editor.show_line_numbers
        screen.action_toggle_wrap()
        screen.action_toggle_line_numbers()
        assert editor.soft_wrap is wrap
        assert editor.show_line_numbers is numbers


@pytest.mark.asyncio
async def test_state_survives_a_hex_round_trip(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_text(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        await pilot.press("f10", "f11")  # wrap on, numbers off (the opposite of the defaults)
        await pilot.pause()
        wrap, numbers = editor.soft_wrap, editor.show_line_numbers
        before = await view_marks(pilot, screen)
        await toggle(pilot, screen)
        await pilot.press("f10", "f11")
        await pilot.pause()
        await toggle(pilot, screen)
        assert (editor.soft_wrap, editor.show_line_numbers) == (wrap, numbers)
        assert await view_marks(pilot, screen) == before
        await pilot.press("f10")
        await pilot.pause()
        assert editor.soft_wrap is not wrap


@pytest.mark.asyncio
async def test_items_are_disabled_when_the_document_starts_in_hex(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_binary(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        assert screen.document.hex_mode
        assert enabled_of(screen) == {"editor.line_numbers": False, "editor.wrap_mode": False, "editor.hex_mode": True}


@pytest.mark.asyncio
async def test_open_updates_the_enabled_state(tmp_path: Path) -> None:
    first = make_text(tmp_path, "a_first.txt")
    make_binary(tmp_path, "b_binary.dat")
    home = tmp_path / "home"
    home.mkdir()
    app = NovaEditApp(file_path=first, file_provider=HomeProvider(home))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        for name, hex_expected in (("b_binary.dat", True), ("a_first.txt", False)):
            await pilot.press("ctrl+o")
            await wait_until(pilot, lambda: open_file_dialog(app) is not None)
            await pick(pilot, tmp_path, name)
            await wait_until(
                pilot,
                lambda expected=hex_expected: screen.document.hex_mode is expected and screen.focused is screen.document.shown,
            )
            assert screen._by_id["editor.wrap_mode"].enabled is not hex_expected
            assert screen._by_id["editor.line_numbers"].enabled is not hex_expected


def painted_disabled(menu: Menu, label: str) -> bool:
    """Whether the painted (cached) row of `label` is drawn disabled; unlike `render_line` this is stale until the menu is refreshed."""
    disabled = menu.get_component_rich_style("disabled")
    strips = menu.render_lines(Region(0, 0, menu.size.width, menu.size.height))
    return any(segment.style is not None and segment.style.color == disabled.color for segment in strips[row_of(menu, label)])


@pytest.mark.asyncio
async def test_an_open_menu_redraws_when_the_mode_changes(tmp_path: Path) -> None:
    """The real switch moves the focus and closes the menu, so the mode flips under the open menu with the focus kept (R7)."""
    app = NovaEditApp(file_path=make_text(tmp_path))
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        menu = await open_view_menu(pilot, screen)
        await pilot.pause()
        assert not painted_disabled(menu, "Line Numbers")
        assert not painted_disabled(menu, "Wrap Mode")
        with patch.object(DocumentView, "hex_mode", new_callable=PropertyMock, return_value=True):
            screen._sync_view_actions()
            await pilot.pause()
            assert menu in list(screen.query(Menu))
            assert painted_disabled(menu, "Line Numbers")
            assert painted_disabled(menu, "Wrap Mode")
            assert not painted_disabled(menu, "Hex Mode")
        with patch.object(DocumentView, "hex_mode", new_callable=PropertyMock, return_value=False):
            screen._sync_view_actions()
            await pilot.pause()
            assert menu in list(screen.query(Menu))
            assert not painted_disabled(menu, "Line Numbers")
            assert not painted_disabled(menu, "Wrap Mode")
