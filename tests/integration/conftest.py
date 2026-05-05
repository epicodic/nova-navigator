"""Shared fixtures and helpers for Nova Navigator integration tests.

Integration tests launch a real NovaNavigator app against a real (temporary)
local filesystem.  Each test gets its own isolated src/dst directory pair via
pytest's ``tmp_path`` fixture, so tests never interfere with each other.

Helpers
-------
set_panels(ctx)
    Point the left panel at ``ctx.src_dir`` and the right panel at
    ``ctx.dst_dir``, then focus the left panel.  Call this after populating
    the source directory so the browser loads the correct listing.

auto_confirm_copy_dialog(filename=None)
    Return a mock ``CopyMoveFilesDialog`` that immediately confirms ("OK").
    Pass *filename* to simulate the user editing the destination filename
    (only relevant for single-file copies).

auto_cancel_dialog()
    Return a mock dialog that immediately cancels.

auto_confirm_delete_dialog()
    Return a mock ``DeleteFilesDialog`` that immediately confirms ("YES").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from textual.pilot import Pilot

from nova_navigator.nova_navigator import MainScreen, NovaNavigator
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem

# ---------------------------------------------------------------------------
# --headed CLI option
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--headed",
        action="store_true",
        default=False,
        help="Run integration tests with a visible Textual app (headless=False).",
    )


@pytest.fixture
def headed(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--headed"))


# ---------------------------------------------------------------------------
# AppCtx — bundles everything a test needs
# ---------------------------------------------------------------------------


@dataclass
class AppCtx:
    """Context object passed to every integration test."""

    pilot: Pilot[None]
    screen: MainScreen
    src_dir: Path
    dst_dir: Path
    fs: LocalFilesystem


# ---------------------------------------------------------------------------
# Async fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_ctx(tmp_path: Path, headed: bool) -> object:  # yields AppCtx
    """Launch NovaNavigator and yield an AppCtx with empty src/dst dirs.

    Pass ``--headed`` on the pytest command line to render the app live in the
    terminal so you can visually inspect what the test is doing.

    The app is torn down cleanly after each test.
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    app = NovaNavigator()
    async with app.run_test(size=(120, 40), headless=not headed) as pilot:
        await pilot.pause()
        screen = app._main_screen
        fs = LocalFilesystem.singleton()
        yield AppCtx(pilot=pilot, screen=screen, src_dir=src, dst_dir=dst, fs=fs)


# ---------------------------------------------------------------------------
# Panel setup helper
# ---------------------------------------------------------------------------


async def set_panels(ctx: AppCtx) -> None:
    """Point panels at ctx.src_dir / ctx.dst_dir and focus the left panel.

    Call this *after* you have written test files into ctx.src_dir so the
    directory browser loads the correct listing on the first scan.

    After the directory is loaded the cursor sits on the ``..`` (parent) entry.
    This helper presses ``down`` once to land on the first real file so that
    ``selected_path_items`` is non-empty even without explicit selection.
    """
    ctx.screen._left_panel.set_path(VPath(ctx.src_dir, ctx.fs))
    ctx.screen._right_panel.set_path(VPath(ctx.dst_dir, ctx.fs))
    ctx.screen._left_panel.focus()
    await ctx.pilot.pause()  # let the directory scan complete
    await ctx.pilot.press("down")  # skip past ".." to first file
    await ctx.pilot.pause()


async def poll_until(
    pilot: Pilot[None],
    predicate: Callable[[], bool],
    *,
    interval: float = 0.1,
    max_wait: float = 5.0,
) -> None:
    """Pause repeatedly until *predicate* returns ``True`` or *max_wait* expires.

    Prefer this over ``pilot.pause(delay=X)`` wherever the ready condition is
    observable, so tests are not sensitive to absolute timing under load.
    """
    steps = max(1, round(max_wait / interval))
    for _ in range(steps):
        await pilot.pause(delay=interval)
        if predicate():
            return


# ---------------------------------------------------------------------------
# Dialog mock helpers
# ---------------------------------------------------------------------------


def auto_confirm_copy_dialog(filename: str | None = None) -> MagicMock:
    """Mock CopyMoveFilesDialog that immediately returns 'OK'.

    Args:
        filename: Simulated edited filename; ``None`` means no rename.
    """
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value="OK")
    dialog.filename = filename
    return dialog


def auto_cancel_dialog() -> MagicMock:
    """Mock dialog that immediately returns 'CANCEL'."""
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value="CANCEL")
    return dialog


def auto_confirm_delete_dialog() -> MagicMock:
    """Mock DeleteFilesDialog that immediately returns 'YES'."""
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value="YES")
    return dialog


def auto_confirm_decision_dialog(decision: object) -> MagicMock:
    """Mock decision dialog that immediately returns *decision*.

    Used to simulate the user answering an overwrite or other in-job
    decision prompt without showing a real screen.

    Args:
        decision: The ``Decision`` value the dialog should return.
    """
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=decision)
    return dialog
