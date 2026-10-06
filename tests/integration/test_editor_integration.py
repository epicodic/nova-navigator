"""F4 opens the built-in editor screen over the file list: local files directly, other files through a local copy (REQ-20, REQ-21)."""

from __future__ import annotations

import asyncio

import pytest

from nova_editor.screen import EditorScreen
from nova_navigator.nova_navigator import MainScreen
from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from nova_widgets import MessageBox
from tests.integration.conftest import AppCtx, poll_until, set_panels


def editor_of(ctx: AppCtx) -> EditorScreen:
    screen = ctx.app.screen
    assert isinstance(screen, EditorScreen)
    return screen


async def open_with_f4(ctx: AppCtx) -> EditorScreen:
    await set_panels(ctx)
    await ctx.pilot.press("f4")
    await poll_until(ctx.pilot, lambda: isinstance(ctx.app.screen, EditorScreen))
    return editor_of(ctx)


async def back_in_the_list(ctx: AppCtx) -> None:
    await poll_until(ctx.pilot, lambda: isinstance(ctx.app.screen, MainScreen))
    assert isinstance(ctx.app.screen, MainScreen)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f4_opens_a_local_file_directly(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")

    screen = await open_with_f4(app_ctx)

    assert screen.document.file_path == app_ctx.src_dir / "a.txt"
    assert app_ctx.app.local_copies.entries == []
    assert [m.text for m in screen.menu_bar.actions] == ["File", "Edit", "Search", "View"]
    assert screen.document.editor.text == "hello\n"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_typing_and_ctrl_s_write_the_local_file(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")
    screen = await open_with_f4(app_ctx)

    await app_ctx.pilot.press("x", "ctrl+s")
    await poll_until(app_ctx.pilot, lambda: (app_ctx.src_dir / "a.txt").read_text() == "xhello\n")

    assert not screen.document.editor.modified


@pytest.mark.asyncio
@pytest.mark.integration
async def test_close_returns_to_the_list_and_reloads_the_panels(app_ctx: AppCtx) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text("hello\n")
    await open_with_f4(app_ctx)
    file.write_text("hello, much longer now\n")
    other = app_ctx.src_dir / "b.txt"
    other.write_text("b")

    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)

    names = [item.name for item in app_ctx.screen.active_panel().items]
    assert "b.txt" in names
    assert app_ctx.app.sub_title == ""


@pytest.mark.asyncio
@pytest.mark.integration
async def test_closing_with_edits_asks_and_cancel_keeps_the_editor(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")
    screen = await open_with_f4(app_ctx)

    await app_ctx.pilot.press("x", "ctrl+w")
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, MessageBox))
    await app_ctx.pilot.press("escape")
    await poll_until(app_ctx.pilot, lambda: app_ctx.app.screen is screen)

    assert screen.document.editor.modified
    await app_ctx.pilot.press("ctrl+w")
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, MessageBox))
    await app_ctx.pilot.press("right", "enter")
    await back_in_the_list(app_ctx)
    assert (app_ctx.src_dir / "a.txt").read_text() == "hello\n"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f4_on_a_file_that_vanished_shows_the_error_dialog_and_opens_no_editor(app_ctx: AppCtx) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text("hello\n")
    await set_panels(app_ctx)
    file.unlink()

    await app_ctx.pilot.press("f4")
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, MessageBox))

    assert not any(isinstance(s, EditorScreen) for s in app_ctx.app.screen_stack)
    await app_ctx.pilot.press("escape")
    await back_in_the_list(app_ctx)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_enter_on_a_text_file_still_runs_the_filetype_command(app_ctx: AppCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")
    commands: list[list[str]] = []

    async def record(args: list[str], _cwd: object) -> None:
        commands.append(args)

    monkeypatch.setattr(app_ctx.app, "execute_command", record)
    await set_panels(app_ctx)

    await app_ctx.pilot.press("enter")
    await poll_until(app_ctx.pilot, lambda: bool(commands))

    assert any("a.txt" in part for part in commands[0])
    assert isinstance(app_ctx.app.screen, MainScreen)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_edit_user_menu_file_opens_the_editor_screen_on_the_config_file(app_ctx: AppCtx) -> None:
    await app_ctx.app.run_action("edit_user_menu", app_ctx.screen)
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, EditorScreen))

    screen = editor_of(app_ctx)

    assert screen.document.file_path == app_ctx.src_dir.parent / "config" / "usermenu.toml"
    assert app_ctx.app.local_copies.entries == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_second_request_while_the_editor_is_opening_pushes_no_second_editor(app_ctx: AppCtx) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text("hello\n")
    source = VPath(file, LocalFilesystem.singleton())

    await asyncio.gather(app_ctx.app.open_editor(source), app_ctx.app.open_editor(source))
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, EditorScreen))
    await app_ctx.pilot.pause()

    assert sum(isinstance(s, EditorScreen) for s in app_ctx.app.screen_stack) == 1
