"""Integration tests for the right-click context menu in Nova Navigator.

The context menu handler (``on_directory_browser_context_menu``) is triggered
by a ``DirectoryBrowser.ContextMenu`` message.  We post it directly rather
than simulating a real right-click, and mock ``Menu.exec`` so we control which
action is "selected" without rendering a real popup.

Note on action dispatch: most context menu actions (Delete, Copy, etc.) do not
have a Textual ``action=`` string — they rely on a TODO in ``_run_action`` to
eventually be wired up directly.  ``show_hidden_files`` is one of the few that
does have ``action="show_hidden_files"``, making it the best end-to-end target
for verifying the full dispatch path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nova_navigator.vfs import VPath
from nova_navigator.widgets import DirectoryBrowser
from nova_widgets.menu import Menu
from tests.integration.conftest import AppCtx, set_panels

_DELETE_DIALOG_PATH = "nova_navigator.filemanager.jobs.DeleteFilesDialog"


def _post_context_menu(ctx: AppCtx, path: VPath | None) -> None:
    """Post a ContextMenu event on the left panel for *path*."""
    ctx.screen._left_panel.post_message(DirectoryBrowser.ContextMenu(ctx.screen._left_panel, path))


# ---------------------------------------------------------------------------
# Dismiss — no action taken
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_context_menu_dismiss_leaves_directory_unchanged(app_ctx: AppCtx) -> None:
    """Dismissing the context menu (exec returns None) has no side effects."""
    (app_ctx.src_dir / "untouched.txt").write_text("data")
    await set_panels(app_ctx)

    file_vpath = VPath(app_ctx.src_dir / "untouched.txt", app_ctx.fs)
    with patch.object(Menu, "exec", new=AsyncMock(return_value=None)):
        _post_context_menu(app_ctx, file_vpath)
        await app_ctx.pilot.pause(delay=0.3)  # type: ignore[union-attr]

    assert (app_ctx.src_dir / "untouched.txt").exists()


# ---------------------------------------------------------------------------
# Action dispatch — _run_action is called when exec returns an action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_context_menu_selected_action_is_dispatched_to_run_action(app_ctx: AppCtx) -> None:
    """When the user selects an item, _run_action is called with that action."""
    (app_ctx.src_dir / "file.txt").write_text("")
    await set_panels(app_ctx)

    file_vpath = VPath(app_ctx.src_dir / "file.txt", app_ctx.fs)
    delete_action = app_ctx.screen._act("file.delete")

    with patch.object(Menu, "exec", new=AsyncMock(return_value=delete_action)):
        with patch.object(app_ctx.screen, "_run_action", new=AsyncMock()) as spy_run_action:
            _post_context_menu(app_ctx, file_vpath)
            await app_ctx.pilot.pause(delay=0.3)  # type: ignore[union-attr]

    spy_run_action.assert_awaited_once_with(delete_action)


# ---------------------------------------------------------------------------
# End-to-end: action with action= wired dispatches to Textual action system
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_context_menu_show_hidden_files_syncs_both_panels(app_ctx: AppCtx) -> None:
    """Selecting 'Show Hidden Files' (which has action='show_hidden_files') enables
    hidden-file display in both panels via _run_action → _action_show_hidden_files.

    The action reads its own checked state, so we pre-set checked=True to make
    _action_show_hidden_files propagate True to the panels.
    """
    await set_panels(app_ctx)

    show_hidden_action = app_ctx.screen._act("view.show_hidden_files")
    show_hidden_action.set_checked(True)  # _action_show_hidden_files reads this

    with patch.object(Menu, "exec", new=AsyncMock(return_value=show_hidden_action)):
        _post_context_menu(app_ctx, None)
        await app_ctx.pilot.pause(delay=0.3)  # type: ignore[union-attr]

    assert app_ctx.screen._left_panel.show_hidden_files
    assert app_ctx.screen._right_panel.show_hidden_files
