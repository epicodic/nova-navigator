"""View-only mode of the editor widget and screen: the three guards hold without the read-only flag, the screen offers no way to write."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from nova_editor.document_view import DocumentView
from nova_editor.editor_actions import build_editor_actions
from nova_editor.timed_text_area import TimedNovaTextArea
from nova_editor.widget import NovaTextArea
from nova_widgets.key_types import KeySequence
from nova_widgets.response import Response

from .helpers_view import wait_until
from .screen_host import EditorScreenHost, write_keys

_WRITE_IDS = {"editor.open", "editor.save", "editor.save_as", "editor.undo", "editor.redo", "editor.cut", "editor.paste"}


def _open(path: Path) -> DocumentView:
    view, error = DocumentView.open(
        path,
        editor_class=TimedNovaTextArea,
        soft_wrap=False,
        show_line_numbers=True,
        config=None,
        timing_file=None,
        view_only=True,
    )
    assert error is None
    return view


class _WidgetApp(App[None]):
    def __init__(self, view: DocumentView) -> None:
        super().__init__()
        self.view = view

    def compose(self) -> ComposeResult:
        yield self.view.editor


def _try_every_programmatic_edit(editor: NovaTextArea) -> None:
    editor.insert("x")
    editor.delete((0, 0), (0, 1))
    editor.replace("y", (0, 0), (0, 1))
    editor.clear()


@pytest.mark.asyncio
async def test_view_only_refuses_save_undo_and_programmatic_edits(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"one\ntwo\n")
    app = _WidgetApp(_open(source))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.view.editor
        assert editor.read_only
        editor.read_only = False  # the lock and the refused save must hold without layer 1
        _try_every_programmatic_edit(editor)
        editor.undo()
        editor.redo()
        await pilot.pause()
        assert editor.text == "one\ntwo\n"
        assert editor.save() is False
        assert editor.save(tmp_path / "other.txt", overwrite=True) is False
    assert not (tmp_path / "other.txt").exists()
    assert source.read_bytes() == b"one\ntwo\n"


@pytest.mark.asyncio
async def test_view_only_lock_survives_reload(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"one\n")
    app = _WidgetApp(_open(source))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.view.editor
        editor.read_only = False
        source.write_bytes(b"changed\n")
        assert editor.reload()
        await pilot.pause()
        editor.insert("x")
        assert editor.text == "changed\n"
        editor.load_text("loaded\n")
        editor.insert("x")
        assert editor.text == "loaded\n"


@pytest.mark.asyncio
async def test_view_only_has_no_write_actions(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"one\n")
    host = EditorScreenHost(source, view_only=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        ids = {a.id for a in screen.ACTIONS}
        assert not ids & _WRITE_IDS
        assert {"editor.reload", "editor.copy", "editor.select_all", "editor.find", "editor.quit", "editor.close"} <= ids
        in_menus = {action.id for menu in screen.menu_bar.actions for action in menu.actions}
        assert {"editor.reload", "editor.copy", "editor.close"} <= in_menus
        assert not in_menus & _WRITE_IDS
        for key in ("ctrl+s", "ctrl+shift+s", "ctrl+o"):
            await screen.press_key(key)
        await pilot.pause()
        assert host.screen is screen  # no dialog opened


@pytest.mark.asyncio
async def test_view_only_external_change_offers_no_write(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"one\n")
    host = EditorScreenHost(source, view_only=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        source.write_bytes(b"something longer\n")
        screen.poll()
        await wait_until(pilot, lambda: host.screen is not screen)
        await pilot.pause()
        buttons = {button.id for button in host.screen.query(Button)}
        assert Response.DISCARD.name in buttons
        assert Response.SAVE.name not in buttons
        assert Response.OVERWRITE.name not in buttons


@pytest.mark.asyncio
async def test_normal_editor_keys_and_actions_are_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"one\n")
    host = EditorScreenHost(source)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        assert [a.id for a in screen.ACTIONS] == [a.id for a in build_editor_actions()]
        wrap = next(a for a in screen.ACTIONS if a.id == "editor.wrap_mode")
        assert wrap.shortcut == KeySequence.parse("f10")
        assert screen.check_action("close_view", ()) is False
        soft_wrap = screen.document.editor.soft_wrap
        assert await screen.press_key("f10")
        await pilot.pause()
        assert screen.document.editor.soft_wrap is not soft_wrap
        await pilot.press("f10")  # the screen binding is inactive
        await pilot.pause()
        assert host.closed == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("open_key", "popup_name"), [("ctrl+f", "find_popup"), ("ctrl+g", "goto_popup")])
async def test_view_only_escape_and_f10_close_popup_first(tmp_path: Path, open_key: str, popup_name: str) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"one\n")
    host = EditorScreenHost(source, view_only=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        popup = getattr(screen, popup_name)
        for close_key in ("escape", "f10"):
            await screen.press_key(open_key)
            await pilot.pause()
            assert popup.display
            await pilot.press(close_key)
            await pilot.pause()
            assert not popup.display
            assert host.closed == 0
        await pilot.press("f10")
        await wait_until(pilot, lambda: host.closed == 1)


@pytest.mark.asyncio
async def test_view_only_close_request_closes_a_visible_popup_instead_of_the_screen(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"one\n")
    host = EditorScreenHost(source, view_only=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        for open_key, popup in (("ctrl+f", screen.find_popup), ("ctrl+g", screen.goto_popup)):
            await screen.press_key(open_key)
            await pilot.pause()
            await screen.action_close_embedded()
            await pilot.pause()
            assert not popup.display
            assert host.closed == 0
        await screen.action_close_embedded()
        await wait_until(pilot, lambda: host.closed == 1)


@pytest.mark.asyncio
async def test_view_only_leaves_f10_to_the_close_binding_despite_user_keys(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"one\n")
    keys = write_keys(tmp_path / "keys", {"editor.find": KeySequence.parse("f10"), "editor.wrap_mode": KeySequence.parse("f9")})
    host = EditorScreenHost(source, keybindings=keys, keyboard_shortcuts_item=False, view_only=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        find = next(a for a in screen.ACTIONS if a.id == "editor.find")
        assert find.shortcut is None
        assert not await screen.press_key("f10")
        soft_wrap = screen.document.editor.soft_wrap
        assert await screen.press_key("f9")  # Wrap Mode works with a user-assigned key
        await pilot.pause()
        assert screen.document.editor.soft_wrap is not soft_wrap
        await pilot.press("f10")
        await wait_until(pilot, lambda: host.closed == 1)


@pytest.mark.asyncio
async def test_view_only_default_wrap_mode_has_no_key(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"one\n")
    host = EditorScreenHost(source, view_only=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        wrap = next(a for a in screen.ACTIONS if a.id == "editor.wrap_mode")
        assert wrap.shortcut is None
        assert not await screen.press_key("f10")


@pytest.mark.asyncio
async def test_view_only_hex_keys_change_nothing(tmp_path: Path) -> None:
    source = tmp_path / "a.bin"
    data = bytes(range(64))
    source.write_bytes(data)
    host = EditorScreenHost(source, view_only=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        assert screen.document.hex_mode
        host.copy_to_clipboard("41 42")
        for key in ("4", "1", "delete", "backspace", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+y", "enter"):
            await screen.press_key(key)
            await pilot.press(key)
        await pilot.pause()
        document = screen.document.editor.document
        assert document.length == len(data)
        assert document.read_bytes(0, document.length) == data
        assert not screen.document.editor.modified
        assert host.screen is screen
    assert source.read_bytes() == data
