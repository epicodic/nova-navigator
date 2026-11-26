"""Integration tests for Select By Pattern and Unselect By Pattern."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_navigator.config import loader
from nova_navigator.config.global_config import conf_
from nova_navigator.config.settings import Settings
from nova_navigator.file_filter import FilenamePatternFilter
from nova_navigator.widgets.directory_browser import DirectoryBrowser
from nova_widgets import Response
from tests.integration.conftest import AppCtx, auto_cancel_dialog, poll_until, set_panels

_DIALOG_PATH = "nova_navigator.nova_navigator.SelectByPatternDialog"


def _confirm(pattern: str, include_folders: bool = False) -> MagicMock:
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=Response.OK)
    dialog.pattern = pattern
    dialog.include_folders = include_folders
    dialog.file_filter = FilenamePatternFilter.from_pattern_string(pattern)
    return dialog


@pytest.fixture
def isolated_settings(app_ctx: AppCtx, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Replace the global settings with ones loaded from the temporary config dir."""
    assert loader._APP_CONFIG_DIR.is_relative_to(app_ctx.src_dir.parent)  # never the developer's real config
    settings = Settings.load()
    monkeypatch.setattr(conf_, "settings", settings)
    return settings


def _selected_names(panel: DirectoryBrowser) -> set[str]:
    return {p.name for p in panel._selected_items}


async def _populate(ctx: AppCtx) -> None:
    for name in ("a.py", "b.txt", "c.md", ".hid.py"):
        (ctx.src_dir / name).write_text(name)
    (ctx.src_dir / "pkg.py").mkdir()
    await set_panels(ctx)
    await poll_until(ctx.pilot, lambda: len(ctx.screen._left_panel._shown_items) >= 4)


def _selection_actions_menu_ids(ctx: AppCtx) -> list[str | None]:
    menu = next(m for m in ctx.screen._menu_bar.actions if m.id == "selection")
    return [a.id for a in menu._actions]


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("isolated_settings")
async def test_ctrl_s_plus_selects_matching_files(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    with patch(_DIALOG_PATH, return_value=_confirm("*.py;*.txt")):
        await app_ctx.pilot.press("ctrl+s", "plus")
        panel = app_ctx.screen._left_panel
        await poll_until(app_ctx.pilot, lambda: bool(panel._selected_items))
    assert _selected_names(app_ctx.screen._left_panel) == {"a.py", "b.txt"}


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("isolated_settings")
async def test_ctrl_s_minus_unselects_matching_files(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    panel = app_ctx.screen._left_panel
    panel.action_select_all()
    before = len(panel._selected_items)
    with patch(_DIALOG_PATH, return_value=_confirm("*.py")):
        await app_ctx.pilot.press("ctrl+s", "minus")
        await poll_until(app_ctx.pilot, lambda: len(panel._selected_items) < before)
    names = _selected_names(panel)
    assert "a.py" not in names
    assert {"b.txt", "c.md"} <= names


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("isolated_settings")
async def test_select_menu_entry_is_wired_and_opens_dialog(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    ids = _selection_actions_menu_ids(app_ctx)
    assert ids.index("selection.select_by_pattern") + 1 == ids.index("selection.unselect_by_pattern")
    panel = app_ctx.screen._left_panel
    with patch(_DIALOG_PATH, return_value=_confirm("*.md")) as dialog_cls:
        await app_ctx.screen._run_action(app_ctx.screen._act("selection.select_by_pattern"))
        await poll_until(app_ctx.pilot, lambda: bool(panel._selected_items))
    assert dialog_cls.call_args.kwargs["select"] is True
    assert _selected_names(panel) == {"c.md"}


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("isolated_settings")
async def test_unselect_menu_entry_opens_dialog_with_select_false(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    panel = app_ctx.screen._left_panel
    panel.action_select_all()
    with patch(_DIALOG_PATH, return_value=_confirm("*.md")) as dialog_cls:
        await app_ctx.screen._run_action(app_ctx.screen._act("selection.unselect_by_pattern"))
        await poll_until(app_ctx.pilot, lambda: "c.md" not in _selected_names(panel))
    assert dialog_cls.call_args.kwargs["select"] is False
    assert "c.md" not in _selected_names(panel)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("isolated_settings")
async def test_cancel_changes_nothing(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    panel = app_ctx.screen._left_panel
    panel._selected_items |= {i for i in panel._shown_items if i.name == "c.md"}
    with patch(_DIALOG_PATH, return_value=auto_cancel_dialog()) as dialog_cls:
        await app_ctx.pilot.press("ctrl+s", "plus")
        await poll_until(app_ctx.pilot, lambda: dialog_cls.called)
        await app_ctx.pilot.pause()
    assert _selected_names(panel) == {"c.md"}
    assert "include_folders = true" not in (loader._APP_CONFIG_DIR / "settings.toml").read_text()
    assert conf_.settings.selection.include_folders is False


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("isolated_settings")
async def test_ok_saves_include_folders_and_shares_it_between_dialogs(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    panel = app_ctx.screen._left_panel
    with patch(_DIALOG_PATH, return_value=_confirm("*.py", include_folders=True)):
        await app_ctx.pilot.press("ctrl+s", "plus")
        await poll_until(app_ctx.pilot, lambda: bool(panel._selected_items))
    assert Settings.load().selection.include_folders is True
    with patch(_DIALOG_PATH, return_value=_confirm("*.zzz", include_folders=True)) as dialog_cls:
        await app_ctx.pilot.press("ctrl+s", "minus")
        await poll_until(app_ctx.pilot, lambda: dialog_cls.called)
    assert dialog_cls.call_args.kwargs["include_folders"] is True


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("isolated_settings")
async def test_default_include_folders_off_is_passed_to_dialog(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    with patch(_DIALOG_PATH, return_value=auto_cancel_dialog()) as dialog_cls:
        await app_ctx.pilot.press("ctrl+s", "plus")
        await poll_until(app_ctx.pilot, lambda: dialog_cls.called)
    assert dialog_cls.call_args.kwargs["include_folders"] is False


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("isolated_settings")
async def test_ok_keeps_other_settings_and_comments(app_ctx: AppCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    (loader._APP_CONFIG_DIR / "settings.toml").write_text("# user note\n[network]\nssh_timeout = 77\n")
    monkeypatch.setattr(conf_, "settings", Settings.load())
    await _populate(app_ctx)
    panel = app_ctx.screen._left_panel
    with patch(_DIALOG_PATH, return_value=_confirm("*.py", include_folders=True)):
        await app_ctx.pilot.press("ctrl+s", "plus")
        await poll_until(app_ctx.pilot, lambda: bool(panel._selected_items))
    text = (loader._APP_CONFIG_DIR / "settings.toml").read_text()
    assert "# user note" in text
    assert "ssh_timeout = 77" in text
    assert "include_folders = true" in text


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("isolated_settings")
async def test_save_failure_still_applies_selection(app_ctx: AppCtx) -> None:
    await _populate(app_ctx)
    panel = app_ctx.screen._left_panel
    with patch.object(Settings, "save", side_effect=OSError), patch(_DIALOG_PATH, return_value=_confirm("*.py")):
        await app_ctx.pilot.press("ctrl+s", "plus")
        await poll_until(app_ctx.pilot, lambda: bool(panel._selected_items))
    assert _selected_names(panel) == {"a.py"}


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("isolated_settings")
async def test_pattern_applies_to_the_active_panel(app_ctx: AppCtx) -> None:
    (app_ctx.dst_dir / "r.py").write_text("r")
    await _populate(app_ctx)
    right = app_ctx.screen._right_panel
    await poll_until(app_ctx.pilot, lambda: len(right._shown_items) >= 2)
    right.focus()
    await app_ctx.pilot.pause()
    with patch(_DIALOG_PATH, return_value=_confirm("*.py")):
        await app_ctx.pilot.press("ctrl+s", "plus")
        await poll_until(app_ctx.pilot, lambda: bool(right._selected_items))
    assert _selected_names(right) == {"r.py"}
    assert _selected_names(app_ctx.screen._left_panel) == set()
