"""Integration tests for the F1 key help dialog."""

from __future__ import annotations

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Input, Static

from nova_navigator.dialogs.key_help_dialog import KeyHelpDialog
from nova_navigator.terminal import Terminal
from nova_widgets.keymap.key_sequence import KeySequence
from nova_widgets.menu import Menu
from nova_widgets.menu._menu_bar import MenuBarItem
from tests.integration.conftest import AppCtx, poll_until


def _help_rows(dialog: KeyHelpDialog) -> list[str]:
    return [str(static.render()) for static in dialog.query(".help-row").results(Static)]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f1_opens_a_scrollable_help(app_ctx: AppCtx) -> None:
    app_ctx.screen._right_panel.focus()
    await app_ctx.pilot.pause()
    await app_ctx.pilot.press("f1")
    await app_ctx.pilot.pause()

    dialog = app_ctx.app.screen
    assert isinstance(dialog, KeyHelpDialog)
    scroll = dialog.query_one(VerticalScroll)
    assert app_ctx.app.focused is scroll
    assert scroll.max_scroll_y > 0
    assert not list(dialog.query(Input))

    before = scroll.scroll_y
    await app_ctx.pilot.press("pagedown")
    await app_ctx.pilot.pause()
    assert scroll.scroll_y > before


@pytest.mark.asyncio
@pytest.mark.integration
async def test_help_shows_user_override_not_default(app_ctx: AppCtx) -> None:
    screen = app_ctx.screen
    screen.keymap_config.save({"browser.copy": KeySequence.parse("f9")})
    screen._reload_keymap()
    await app_ctx.pilot.press("f1")
    await app_ctx.pilot.pause()

    dialog = app_ctx.app.screen
    assert isinstance(dialog, KeyHelpDialog)
    copy_rows = [row for group in dialog.groups for row in group.rows if row.action_id == "browser.copy"]
    assert [row.keys for row in copy_rows] == ["F9"]
    rendered = _help_rows(dialog)
    assert any(text.startswith("F9") and text.endswith("Copy") for text in rendered)
    assert not any(text.startswith("F5") and text.endswith("Copy") for text in rendered)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_escape_and_f1_close_help_and_restore_focus(app_ctx: AppCtx) -> None:
    screen = app_ctx.screen
    for key in ("escape", "f1"):
        screen._right_panel.focus()
        await app_ctx.pilot.pause()
        await app_ctx.pilot.press("f1")
        await app_ctx.pilot.pause()
        assert isinstance(app_ctx.app.screen, KeyHelpDialog)
        await app_ctx.pilot.press(key)
        await app_ctx.pilot.pause()
        assert app_ctx.app.screen is screen
        assert screen.focused is screen._right_panel


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f1_with_terminal_focused_goes_to_the_shell(app_ctx: AppCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = app_ctx.screen.query(Terminal).first()
    terminal.focus()
    await poll_until(app_ctx.pilot, lambda: terminal.send_queue is not None)
    sent: list[object] = []
    assert terminal.send_queue is not None
    monkeypatch.setattr(terminal.send_queue, "put_nowait", sent.append)
    await app_ctx.pilot.press("f1")
    await app_ctx.pilot.pause()
    assert ["stdin", "\x1bOP"] in sent
    assert app_ctx.app.screen is app_ctx.screen


async def _open_icon_menu_with_mouse(app_ctx: AppCtx) -> Menu:
    menu_bar = app_ctx.screen._menu_bar
    await app_ctx.pilot.click(menu_bar.query(MenuBarItem).first())
    await app_ctx.pilot.pause()
    menu = menu_bar._menus[0]
    assert menu.is_mounted
    return menu


async def _click_help_row(app_ctx: AppCtx, menu: Menu) -> None:
    help_index = next(i for i, action in enumerate(menu._actions) if action.id == "app.help")
    await app_ctx.pilot.hover(menu, offset=(2, help_index + 1))
    await app_ctx.pilot.click(menu, offset=(2, help_index + 1))
    await app_ctx.pilot.pause()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_help_from_menu_restores_previous_focus(app_ctx: AppCtx) -> None:
    screen = app_ctx.screen
    screen._right_panel.focus()
    await app_ctx.pilot.pause()
    menu = await _open_icon_menu_with_mouse(app_ctx)
    await _click_help_row(app_ctx, menu)
    assert isinstance(app_ctx.app.screen, KeyHelpDialog)
    await app_ctx.pilot.press("escape")
    await app_ctx.pilot.pause()
    assert app_ctx.app.screen is screen
    assert screen.focused is screen._right_panel

    terminal = screen.query(Terminal).first()
    terminal.focus()
    await app_ctx.pilot.pause()
    menu = await _open_icon_menu_with_mouse(app_ctx)
    await _click_help_row(app_ctx, menu)
    assert isinstance(app_ctx.app.screen, KeyHelpDialog)
    await app_ctx.pilot.press("escape")
    await app_ctx.pilot.pause()
    assert screen.focused is terminal
