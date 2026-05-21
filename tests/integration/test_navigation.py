"""Integration tests for navigation, panel management, and UI modes in Nova Navigator."""

from __future__ import annotations

import pytest

from tests.integration.conftest import AppCtx, set_panels

# ---------------------------------------------------------------------------
# Panel toggle (Tab)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_tab_switches_focus_to_right_panel(app_ctx: AppCtx) -> None:
    """Tab moves focus from the left panel to the right panel."""
    (app_ctx.src_dir / "file.txt").write_text("")
    await set_panels(app_ctx)
    assert app_ctx.screen._last_active_panel is app_ctx.screen._left_panel

    await app_ctx.pilot.press("tab")
    await app_ctx.pilot.pause()

    assert app_ctx.screen._last_active_panel is app_ctx.screen._right_panel


@pytest.mark.asyncio
@pytest.mark.integration
async def test_tab_twice_returns_focus_to_left_panel(app_ctx: AppCtx) -> None:
    """Pressing Tab twice returns focus to the left panel."""
    (app_ctx.src_dir / "file.txt").write_text("")
    await set_panels(app_ctx)

    await app_ctx.pilot.press("tab")
    await app_ctx.pilot.press("tab")
    await app_ctx.pilot.pause()

    assert app_ctx.screen._last_active_panel is app_ctx.screen._left_panel


# ---------------------------------------------------------------------------
# Navigate into directory (Enter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_enter_on_directory_navigates_panel_into_it(app_ctx: AppCtx) -> None:
    """Pressing Enter on a directory opens it in the active panel."""
    subdir = app_ctx.src_dir / "subdir"
    subdir.mkdir()
    await set_panels(app_ctx)  # cursor lands on "subdir" (only entry)

    await app_ctx.pilot.press("enter")
    await app_ctx.pilot.pause(delay=0.2)

    assert app_ctx.screen._left_panel.path.path == subdir


# ---------------------------------------------------------------------------
# Terminal mode cycling (Ctrl+L and Ctrl+O)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ctrl_l_cycles_terminal_between_minimized_and_enlarged(app_ctx: AppCtx) -> None:
    """Ctrl+L toggles the terminal between MINIMIZED and ENLARGED."""
    _Mode = app_ctx.screen._TerminalMode
    assert app_ctx.screen._terminal_mode == _Mode.MINIMIZED

    await app_ctx.pilot.press("ctrl+l")
    await app_ctx.pilot.pause()
    assert app_ctx.screen._terminal_mode == _Mode.ENLARGED

    await app_ctx.pilot.press("ctrl+l")
    await app_ctx.pilot.pause()
    assert app_ctx.screen._terminal_mode == _Mode.MINIMIZED


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ctrl_o_cycles_terminal_between_minimized_and_maximized(app_ctx: AppCtx) -> None:
    """Ctrl+O toggles the terminal between MINIMIZED and MAXIMIZED."""
    _Mode = app_ctx.screen._TerminalMode
    assert app_ctx.screen._terminal_mode == _Mode.MINIMIZED

    await app_ctx.pilot.press("ctrl+o")
    await app_ctx.pilot.pause()
    assert app_ctx.screen._terminal_mode == _Mode.MAXIMIZED

    await app_ctx.pilot.press("ctrl+o")
    await app_ctx.pilot.pause()
    assert app_ctx.screen._terminal_mode == _Mode.MINIMIZED


@pytest.mark.asyncio
@pytest.mark.integration
async def test_enlarged_terminal_sets_height_to_half_screen(app_ctx: AppCtx) -> None:
    """In ENLARGED mode the terminal height is set to half the screen height."""
    await app_ctx.pilot.press("ctrl+l")
    await app_ctx.pilot.pause()

    expected = app_ctx.screen.size.height // 2
    assert app_ctx.screen._terminal_pool.active_terminal.styles.height.value == expected


@pytest.mark.asyncio
@pytest.mark.integration
async def test_maximized_terminal_sets_height_to_screen_minus_two(app_ctx: AppCtx) -> None:
    """In MAXIMIZED mode the terminal height is screen height minus 2."""
    await app_ctx.pilot.press("ctrl+o")
    await app_ctx.pilot.pause()

    expected = app_ctx.screen.size.height - 2
    assert app_ctx.screen._terminal_pool.active_terminal.styles.height.value == expected


# ---------------------------------------------------------------------------
# Toggle hidden files (Ctrl+H)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ctrl_h_enables_hidden_files_in_both_panels(app_ctx: AppCtx) -> None:
    """Ctrl+H turns on hidden-file display in both panels."""
    assert not app_ctx.screen._left_panel.show_hidden_files
    assert not app_ctx.screen._right_panel.show_hidden_files

    await app_ctx.pilot.press("ctrl+h")
    await app_ctx.pilot.pause()

    assert app_ctx.screen._left_panel.show_hidden_files
    assert app_ctx.screen._right_panel.show_hidden_files


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ctrl_h_twice_disables_hidden_files_again(app_ctx: AppCtx) -> None:
    """Pressing Ctrl+H twice returns both panels to hide-hidden-files mode."""
    await app_ctx.pilot.press("ctrl+h")
    await app_ctx.pilot.press("ctrl+h")
    await app_ctx.pilot.pause()

    assert not app_ctx.screen._left_panel.show_hidden_files
    assert not app_ctx.screen._right_panel.show_hidden_files


# ---------------------------------------------------------------------------
# Show processes dialog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_show_processes_makes_jobs_dialog_visible(app_ctx: AppCtx) -> None:
    """action_show_processes() makes the jobs dialog visible and focused."""
    assert not app_ctx.screen._jobs_dialog.display

    app_ctx.screen.action_show_processes()
    await app_ctx.pilot.pause()

    assert app_ctx.screen._jobs_dialog.display
