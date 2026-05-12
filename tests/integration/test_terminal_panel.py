"""Integration tests for the interplay between the directory browser and the terminal.

Two directions are tested:

1. Panel → Terminal: navigating the panel to a new directory triggers a
   ``request_cd`` on the terminal so the shell follows.

2. Terminal → Panel: a user-initiated ``cd`` in the shell (signalled via a
   ``Terminal.PathChanged`` message with ``user_initiated=True``) causes the
   active panel to navigate to the new directory.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import patch

import pytest

from nova_navigator.terminal import Terminal
from nova_navigator.vfs import VPath
from tests.integration.conftest import AppCtx, set_panels

# ---------------------------------------------------------------------------
# Panel → Terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_panel_navigation_triggers_terminal_request_cd(app_ctx: AppCtx) -> None:
    """Navigating the panel to a new directory calls request_cd on the terminal.

    Flow: set_path → DirectoryBrowser.PathChanged →
          MainScreen._on_directory_browser_path_changed →
          _set_terminal_directory → terminal.request_cd
    """
    subdir = app_ctx.src_dir / "subdir"
    subdir.mkdir()
    await set_panels(app_ctx)

    with patch.object(app_ctx.screen._terminal, "request_cd") as mock_cd:
        app_ctx.screen._left_panel.set_path(VPath(subdir, app_ctx.fs))
        await app_ctx.pilot.pause()  # type: ignore[union-attr]

    mock_cd.assert_called_once_with(PurePosixPath(subdir))


# ---------------------------------------------------------------------------
# Terminal → Panel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_user_cd_in_terminal_updates_active_panel(app_ctx: AppCtx) -> None:
    """A user-initiated cd in the terminal navigates the active panel.

    Flow: Terminal.PathChanged(user_initiated=True) →
          MainScreen._on_terminal_path_changed → active_panel().set_path
    """
    target = app_ctx.dst_dir  # a real directory distinct from the panel's current path
    await set_panels(app_ctx)

    app_ctx.screen._terminal.post_message(
        Terminal.PathChanged(app_ctx.screen._terminal, PurePosixPath(target), user_initiated=True)
    )
    await app_ctx.pilot.pause(delay=0.2)  # type: ignore[union-attr]

    assert app_ctx.screen.active_panel().path.path == PurePosixPath(target)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_programmatic_cd_does_not_update_panel(app_ctx: AppCtx) -> None:
    """A programmatic cd (user_initiated=False) does not change the active panel.

    Flow: Terminal.PathChanged(user_initiated=False) →
          MainScreen._on_terminal_path_changed → handler exits early
    """
    await set_panels(app_ctx)
    original_path = app_ctx.screen.active_panel().path.path

    app_ctx.screen._terminal.post_message(
        Terminal.PathChanged(
            app_ctx.screen._terminal,
            PurePosixPath(app_ctx.dst_dir),
            user_initiated=False,
        )
    )
    await app_ctx.pilot.pause(delay=0.2)  # type: ignore[union-attr]

    assert app_ctx.screen.active_panel().path.path == original_path
