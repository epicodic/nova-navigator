"""Editor key bindings in the navigator: the dialog lists them, the config stores them, the embedded editor applies them (REQ-7)."""

from __future__ import annotations

import pytest
from textual import events

from nova_editor.screen import EditorScreen
from nova_navigator.widgets.directory_browser import GoToPathWidget
from nova_widgets import FileDialog, MessageBox
from tests.integration.conftest import AppCtx, poll_until
from tests.integration.test_editor_integration import back_in_the_list, editor_of, open_with_f4


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_equal_default_key_goes_to_the_screen_on_top(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")
    screen = await open_with_f4(app_ctx)

    await app_ctx.pilot.press("ctrl+g")
    await poll_until(app_ctx.pilot, lambda: screen.goto_popup.display)
    assert not list(screen.query(GoToPathWidget))
    await app_ctx.pilot.press("escape", "ctrl+w")
    await back_in_the_list(app_ctx)

    await app_ctx.pilot.press("ctrl+g")
    await poll_until(app_ctx.pilot, lambda: any(w.display for w in app_ctx.screen.query(GoToPathWidget)))


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ctrl_o_opens_the_editors_file_dialog_not_the_navigators_terminal_key(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")
    await open_with_f4(app_ctx)

    await app_ctx.pilot.press("ctrl+o")

    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, FileDialog))
    await app_ctx.pilot.press("escape")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_quit_without_an_editor_still_exits_the_app(app_ctx: AppCtx) -> None:
    await app_ctx.app.action_quit()
    await poll_until(app_ctx.pilot, lambda: not app_ctx.app.is_running)
    assert not app_ctx.app.is_running


@pytest.mark.asyncio
@pytest.mark.integration
async def test_getting_the_terminal_focus_back_checks_the_file(app_ctx: AppCtx) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text("hello\n")
    screen = await open_with_f4(app_ctx)
    assert isinstance(editor_of(app_ctx), EditorScreen)
    file.write_text("hello, changed on disk behind the editor\n")

    app_ctx.app.post_message(events.AppFocus())

    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, MessageBox))
    assert screen in app_ctx.app.screen_stack
