"""Integration tests for navigation, panel management, and UI modes in Nova Navigator."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual import events

from nova_navigator.vfs import VPath
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


# ---------------------------------------------------------------------------
# Terminal sync and Enter routing
# ---------------------------------------------------------------------------


async def _wait_for_terminal_cwd(app_ctx: AppCtx, expected: Path, max_wait: float = 8.0) -> None:
    """Poll until the active terminal reports *expected* as its cwd."""
    terminal = app_ctx.screen._terminal_pool.active_terminal
    deadline = asyncio.get_running_loop().time() + max_wait
    while asyncio.get_running_loop().time() < deadline:
        if terminal._cwd == expected:
            return
        await app_ctx.pilot.pause(delay=0.1)
    raise AssertionError(f"terminal cwd is {terminal._cwd}, expected {expected}")


async def _wait_for_default_load(app_ctx: AppCtx, settle: float = 1.5) -> None:
    """Wait for the panels' default startup listing (the real cwd) to finish loading.

    Panels start pointed at the process's real working directory. Navigating
    away immediately can race that in-flight listing: its late completion
    re-commits the original path and reissues a stray terminal sync after the
    test's own navigation. Waiting for it to settle first avoids that.
    """
    await app_ctx.pilot.pause(delay=settle)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_enter_with_typed_text_in_minimized_mode_executes_in_terminal(app_ctx: AppCtx) -> None:
    """Typed text plus Enter runs the command in the one-line terminal instead of navigating."""
    subdir = app_ctx.src_dir / "subdir"
    subdir.mkdir()
    await _wait_for_default_load(app_ctx)
    await set_panels(app_ctx)
    await _wait_for_terminal_cwd(app_ctx, app_ctx.src_dir)
    assert app_ctx.screen._terminal_mode is app_ctx.screen._TerminalMode.MINIMIZED

    for key in "echo":
        await app_ctx.pilot.press(key)
    # Pilot.press("enter") leaves character=None (a synthesis quirk of the test
    # harness); real keyboard input always carries the raw "\r", so inject that
    # directly the same way Pilot itself feeds keys to the driver.
    enter_event = events.Key("enter", "\r")
    enter_event.set_sender(app_ctx.app)
    driver = app_ctx.app._driver
    assert driver is not None
    driver.send_message(enter_event)
    await app_ctx.pilot.pause(delay=0.5)

    assert app_ctx.screen._left_panel.path.path == app_ctx.src_dir
    assert app_ctx.screen._terminal_pool.active_terminal.has_input() is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_tab_syncs_terminal_to_right_panel(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "file.txt").write_text("")
    await _wait_for_default_load(app_ctx)
    await set_panels(app_ctx)
    await _wait_for_terminal_cwd(app_ctx, app_ctx.src_dir)

    await app_ctx.pilot.press("tab")
    await _wait_for_terminal_cwd(app_ctx, app_ctx.dst_dir)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_path_change_in_inactive_panel_does_not_move_terminal(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "file.txt").write_text("")
    other = app_ctx.dst_dir / "elsewhere"
    other.mkdir()
    await _wait_for_default_load(app_ctx)
    await set_panels(app_ctx)
    await _wait_for_terminal_cwd(app_ctx, app_ctx.src_dir)

    app_ctx.screen._right_panel.set_path(VPath(other, app_ctx.fs))
    await app_ctx.pilot.pause(delay=0.5)

    assert app_ctx.screen._terminal_pool.active_terminal._cwd == app_ctx.src_dir


@pytest.mark.asyncio
@pytest.mark.integration
async def test_user_cd_updates_the_panel_that_owned_the_command(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "file.txt").write_text("")
    target = app_ctx.src_dir / "typed"
    target.mkdir()
    await _wait_for_default_load(app_ctx)
    await set_panels(app_ctx)
    terminal = app_ctx.screen._terminal_pool.active_terminal
    await _wait_for_terminal_cwd(app_ctx, app_ctx.src_dir)

    await terminal.send(f"cd {target}\n")
    await app_ctx.pilot.press("tab")  # switch before the shell reports the new cwd

    deadline = asyncio.get_running_loop().time() + 8.0
    while asyncio.get_running_loop().time() < deadline:
        if app_ctx.screen._left_panel.path.path == target:
            break
        await app_ctx.pilot.pause(delay=0.1)
    assert app_ctx.screen._left_panel.path.path == target
    assert app_ctx.screen._right_panel.path.path == app_ctx.dst_dir
    await _wait_for_terminal_cwd(app_ctx, app_ctx.dst_dir)


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
    height_style = app_ctx.screen._terminal_pool.active_terminal.styles.height
    assert height_style is not None
    assert height_style.value == expected


@pytest.mark.asyncio
@pytest.mark.integration
async def test_maximized_terminal_sets_height_to_screen_minus_two(app_ctx: AppCtx) -> None:
    """In MAXIMIZED mode the terminal height is screen height minus 2."""
    await app_ctx.pilot.press("ctrl+o")
    await app_ctx.pilot.pause()

    expected = app_ctx.screen.size.height - 2
    height_style = app_ctx.screen._terminal_pool.active_terminal.styles.height
    assert height_style is not None
    assert height_style.value == expected


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
