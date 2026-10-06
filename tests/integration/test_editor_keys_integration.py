"""Editor key bindings in the navigator: the dialog lists them, the config stores them, the embedded editor applies them (REQ-7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.text import Text
from textual import events

from nova_editor.screen import EditorScreen
from nova_navigator.embedded_editor_keys import editor_key_actions
from nova_navigator.nova_navigator import MainScreen
from nova_navigator.widgets.directory_browser import GoToPathWidget
from nova_widgets import DataTable, FileDialog, KeybindingsConfig, KeybindingsDialog, MessageBox
from nova_widgets.key_types import KeySequence
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


def config_dir_of(ctx: AppCtx) -> Path:
    return ctx.src_dir.parent / "config"


async def open_dialog(ctx: AppCtx) -> KeybindingsDialog:
    await ctx.app.run_action("keybindings", ctx.screen)
    await poll_until(ctx.pilot, lambda: isinstance(ctx.app.screen, KeybindingsDialog))
    dialog = ctx.app.screen
    assert isinstance(dialog, KeybindingsDialog)
    return dialog


def dialog_labels(dialog: KeybindingsDialog) -> list[str]:
    table = dialog.query(DataTable).first()
    labels: list[str] = []
    for index in range(table.row_count):
        label, _keys = table.get_row_at(index)
        labels.append(label.plain if isinstance(label, Text) else str(label))
    return labels


def saved_bindings(ctx: AppCtx) -> dict[str, str]:
    """The effective key of every action in the navigator's and the editor's lists, read from a fresh load of the file."""
    config = KeybindingsConfig(config_dir_of(ctx))
    return {name: str(keys) for name, keys in config.resolve([*MainScreen.ACTIONS, *editor_key_actions()]).items()}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_dialog_lists_the_editor_actions_after_the_navigators(app_ctx: AppCtx) -> None:
    dialog = await open_dialog(app_ctx)

    labels = dialog_labels(dialog)

    navigator_count = len(MainScreen.ACTIONS)
    assert len(labels) == navigator_count + 18
    assert labels[:navigator_count] == [action.text for action in MainScreen.ACTIONS]
    assert labels[navigator_count] == "Editor: Open…"
    assert "Editor: Save As…" in labels
    await app_ctx.pilot.press("escape")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_accepting_the_dialog_keeps_editor_and_navigator_overrides_in_the_file(app_ctx: AppCtx) -> None:
    app_ctx.screen.keymap_config.save({"editor.save": None, "editor.find": KeySequence.parse("ctrl+k"), "app.quit": KeySequence.parse("ctrl+shift+q")})
    await open_dialog(app_ctx)

    await app_ctx.pilot.press("enter")
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, MainScreen))

    saved = saved_bindings(app_ctx)
    assert "editor.save" not in saved
    assert saved["editor.find"] == "ctrl+k"
    assert saved["app.quit"] == "ctrl+shift+q"
    assert saved["editor.open"] == "ctrl+o"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_override_moves_a_key_of_the_next_editor(app_ctx: AppCtx) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text("hello\n")
    app_ctx.screen.keymap_config.save({"editor.save": KeySequence.parse("ctrl+k")})

    screen = await open_with_f4(app_ctx)

    labels = {a.id: a.shortcut_label for a in screen.ACTIONS}
    assert labels["editor.save"] == "Ctrl+K"
    await app_ctx.pilot.press("x", "ctrl+s")
    await app_ctx.pilot.pause()
    assert file.read_text() == "hello\n"
    assert screen.document.editor.modified
    await app_ctx.pilot.press("ctrl+k")
    await poll_until(app_ctx.pilot, lambda: file.read_text() == "xhello\n")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_empty_override_unmaps_the_key_and_the_label(app_ctx: AppCtx) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text("hello\n")
    app_ctx.screen.keymap_config.save({"editor.save": None})

    screen = await open_with_f4(app_ctx)

    assert {a.id: a.shortcut_label for a in screen.ACTIONS}["editor.save"] == ""
    await app_ctx.pilot.press("x", "ctrl+s")
    await app_ctx.pilot.pause()
    assert file.read_text() == "hello\n"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_embedded_editor_offers_no_keyboard_shortcuts_item(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")

    screen = await open_with_f4(app_ctx)

    assert len(screen.ACTIONS) == 18
    assert [a.id for a in screen.menu_bar.actions[3].actions] == ["editor.line_numbers", "editor.wrap_mode"]
    assert not (config_dir_of(app_ctx) / "keybindings.toml").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ctrl_q_with_the_editor_key_unmapped_still_asks_about_unsaved_text(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")
    app_ctx.screen.keymap_config.save({"editor.quit": None})
    screen = await open_with_f4(app_ctx)

    await app_ctx.pilot.press("x", "ctrl+q")

    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, MessageBox))
    assert app_ctx.app.is_running
    await app_ctx.pilot.press("escape")
    await poll_until(app_ctx.pilot, lambda: app_ctx.app.screen is screen)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ctrl_q_with_the_editor_key_unmapped_closes_an_unmodified_editor_only(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")
    app_ctx.screen.keymap_config.save({"editor.quit": None})
    await open_with_f4(app_ctx)

    await app_ctx.pilot.press("ctrl+q")

    await back_in_the_list(app_ctx)
