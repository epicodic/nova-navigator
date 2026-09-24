"""Integration tests for the F2 user menu."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from nova_navigator.commands import Command, CommandMode
from nova_navigator.response import Response
from nova_navigator.usermenu.context import FileInfo
from nova_navigator.usermenu.popup import UserMenuPopup
from nova_widgets.key_types import KeySequence
from nova_widgets.menu import Action
from tests.integration.conftest import AppCtx, poll_until, set_panels

_INPUT_DIALOG_PATH = "nova_navigator.nova_navigator.UserMenuInputDialog"


def _write_menu(ctx: AppCtx, text: str) -> Path:
    path = ctx.src_dir.parent / "config" / "usermenu.toml"
    path.write_text(text)
    return path


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f2_opens_menu_and_hotkey_runs_background_entry(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("")
    _write_menu(app_ctx, '[touch]\nkey = "t"\nlabel = "Touch"\nmode = "background"\nrun = "touch marker-{file.stem}.txt"\n')
    await set_panels(app_ctx)

    await app_ctx.pilot.press("f2")
    await poll_until(app_ctx.pilot, lambda: type(app_ctx.app.screen).__name__ == "MenuScreen")
    await app_ctx.pilot.press("t")
    await poll_until(app_ctx.pilot, lambda: (app_ctx.src_dir / "marker-a.txt").exists())

    assert (app_ctx.src_dir / "marker-a.txt").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_terminal_entry_keeps_current_terminal_layout_and_focus(app_ctx: AppCtx) -> None:
    _write_menu(app_ctx, '[probe]\nlabel = "Probe"\nrun = "sleep 1"\n')
    await set_panels(app_ctx)
    terminal = app_ctx.screen._terminal_pool.active_terminal
    await poll_until(app_ctx.pilot, lambda: terminal._at_prompt)
    await app_ctx.pilot.pause()
    previous_mode = app_ctx.screen._terminal_mode
    previous_size = terminal.size
    previous_focus = app_ctx.app.focused

    with patch.object(UserMenuPopup, "exec", new=AsyncMock(return_value=Action("Probe", id="probe"))):
        await app_ctx.pilot.app.run_action("user_menu", app_ctx.screen)
        await poll_until(app_ctx.pilot, lambda: terminal._run_future is not None)

        assert app_ctx.screen._terminal_mode == previous_mode
        assert terminal.size == previous_size
        assert app_ctx.app.focused is previous_focus


@pytest.mark.asyncio
@pytest.mark.integration
async def test_short_terminal_command_output_remains_in_scrollback(app_ctx: AppCtx) -> None:
    terminal = app_ctx.screen._terminal_pool.active_terminal
    await poll_until(app_ctx.pilot, lambda: terminal._at_prompt)
    await app_ctx.pilot.pause()

    await app_ctx.screen.command_runner.run(Command("printf 'MENU-MARKER\\n'", app_ctx.fs.path(app_ctx.src_dir), "Probe"), CommandMode.TERMINAL)
    await app_ctx.pilot.pause()

    assert app_ctx.screen._terminal_mode == app_ctx.screen._TerminalMode.MINIMIZED
    assert terminal.size.height == 1
    assert any("MENU-MARKER" in str(line) for line in terminal._history)

    app_ctx.screen.action_toggle_maximized_terminal()
    await app_ctx.pilot.pause()
    assert any("MENU-MARKER" in terminal.render_line(y).text for y in range(terminal.size.height))


@pytest.mark.asyncio
@pytest.mark.integration
async def test_terminal_command_after_existing_scrollback_is_visible_on_reopen(app_ctx: AppCtx) -> None:
    terminal = app_ctx.screen._terminal_pool.active_terminal
    await poll_until(app_ctx.pilot, lambda: terminal._at_prompt)
    await app_ctx.pilot.pause()

    command = Command("seq 1 60; printf 'LATEST-MARKER\\n'", app_ctx.fs.path(app_ctx.src_dir), "Probe")
    await app_ctx.screen.command_runner.run(command, CommandMode.TERMINAL)
    await app_ctx.pilot.pause()

    app_ctx.screen.action_toggle_maximized_terminal()
    await app_ctx.pilot.pause()
    assert any("LATEST-MARKER" in terminal.render_line(y).text for y in range(terminal.size.height))


@pytest.mark.asyncio
@pytest.mark.integration
async def test_no_visible_entries_does_not_open_popup(app_ctx: AppCtx) -> None:
    _write_menu(app_ctx, '[never]\nlabel = "Never"\nrun = "true"\nwhen = "False"\n')
    await set_panels(app_ctx)

    with patch.object(UserMenuPopup, "exec", new=AsyncMock(return_value=None)) as exec_mock:
        await app_ctx.pilot.app.run_action("user_menu", app_ctx.screen)
        await app_ctx.pilot.pause(delay=0.3)

    exec_mock.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_input_values_are_substituted(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("")
    _write_menu(
        app_ctx,
        '[mk]\nlabel = "Make file"\nmode = "background"\nrun = "touch {name}"\n[[mk.input]]\nname = "name"\nprompt = "Name"\ndefault = "{file.stem}.out"\n',
    )
    await set_panels(app_ctx)

    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=Response.OK)
    dialog.values = {"name": "my file.out"}
    with (
        patch.object(UserMenuPopup, "exec", new=AsyncMock(return_value=Action("Make file", id="mk"))),
        patch(_INPUT_DIALOG_PATH, return_value=dialog) as dialog_cls,
    ):
        await app_ctx.pilot.app.run_action("user_menu", app_ctx.screen)
        await poll_until(app_ctx.pilot, lambda: (app_ctx.src_dir / "my file.out").exists())

    fields = dialog_cls.call_args.args[1]
    assert fields[0].value == "a.out"
    assert (app_ctx.src_dir / "my file.out").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cancelled_input_dialog_runs_nothing(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("")
    _write_menu(
        app_ctx,
        '[mk]\nlabel = "Make file"\nmode = "background"\nrun = "touch never.txt"\n[[mk.input]]\nname = "name"\nprompt = "Name"\n',
    )
    await set_panels(app_ctx)

    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=Response.CANCEL)
    with (
        patch.object(UserMenuPopup, "exec", new=AsyncMock(return_value=Action("Make file", id="mk"))),
        patch(_INPUT_DIALOG_PATH, return_value=dialog),
    ):
        await app_ctx.pilot.app.run_action("user_menu", app_ctx.screen)
        await app_ctx.pilot.pause(delay=0.5)

    assert not (app_ctx.src_dir / "never.txt").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_placeholder_io_error_shows_message_instead_of_crashing(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("")
    _write_menu(app_ctx, '[touch]\nlabel = "Touch"\nmode = "background"\nrun = "touch x-{file.size}"\n')
    await set_panels(app_ctx)

    message_box = MagicMock()
    message_box.run = AsyncMock(return_value=Response.OK)
    with (
        patch.object(FileInfo, "size", new_callable=PropertyMock, side_effect=OSError("gone")),
        patch.object(UserMenuPopup, "exec", new=AsyncMock(return_value=Action("Touch", id="touch"))),
        patch("nova_navigator.nova_navigator.MessageBox", return_value=message_box) as message_box_cls,
    ):
        await app_ctx.pilot.app.run_action("user_menu", app_ctx.screen)
        await poll_until(app_ctx.pilot, lambda: message_box_cls.called)

    assert message_box_cls.called
    message = message_box_cls.call_args.args[0]
    assert "gone" in message
    assert app_ctx.app.is_running


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rename_moved_to_shift_f6(app_ctx: AppCtx) -> None:
    assert app_ctx.screen._act("browser.rename").initial_shortcut == KeySequence.parse("shift+f6")
    assert app_ctx.screen._act("app.user_menu").initial_shortcut == KeySequence.parse("f2")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_edit_user_menu_creates_file(app_ctx: AppCtx) -> None:
    path = app_ctx.src_dir.parent / "config" / "usermenu.toml"
    with patch.object(type(app_ctx.app), "open_editor", new=AsyncMock()) as open_editor:
        await app_ctx.pilot.app.run_action("edit_user_menu", app_ctx.screen)
        await app_ctx.pilot.pause()

    assert path.exists()
    opened = open_editor.call_args.args[0]
    assert str(opened.path) == str(path)
