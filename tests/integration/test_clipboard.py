"""Integration tests for Cut / Copy / Paste actions."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nova_navigator.clipboard import ClipboardOperation
from tests.integration.conftest import (
    AppCtx,
    auto_cancel_dialog,
    auto_confirm_copy_dialog,
    poll_until,
    set_panels,
)

_COPY_DIALOG_PATH = "nova_navigator.filemanager.jobs.CopyMoveFilesDialog"


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_sets_clipboard_with_copy_operation(app_ctx: AppCtx) -> None:
    """_action_copy stores the cursor item in the clipboard as COPY."""
    (app_ctx.src_dir / "file.txt").write_text("hello")
    await set_panels(app_ctx)

    await app_ctx.pilot.app.run_action("copy", app_ctx.screen)
    await app_ctx.pilot.pause()

    cb = app_ctx.app._path_clipboard
    assert not cb.empty()
    paths, op = cb.get()
    assert op == ClipboardOperation.COPY
    assert paths[0].name == "file.txt"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_on_parent_entry_does_nothing(app_ctx: AppCtx) -> None:
    """_action_copy is a no-op when the cursor is on the '..' entry."""
    from nova_navigator.vfs import VPath

    (app_ctx.src_dir / "file.txt").write_text("")
    app_ctx.screen._left_panel.set_path(VPath(app_ctx.src_dir, app_ctx.fs))
    app_ctx.screen._right_panel.set_path(VPath(app_ctx.dst_dir, app_ctx.fs))
    app_ctx.screen._left_panel.focus()
    await app_ctx.pilot.pause()
    # cursor is on '..' — do NOT press down

    await app_ctx.pilot.app.run_action("copy", app_ctx.screen)
    await app_ctx.pilot.pause()

    assert app_ctx.app._path_clipboard.empty()


# ---------------------------------------------------------------------------
# Cut
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cut_sets_clipboard_with_cut_operation(app_ctx: AppCtx) -> None:
    """_action_cut stores the cursor item in the clipboard as CUT."""
    (app_ctx.src_dir / "file.txt").write_text("hello")
    await set_panels(app_ctx)

    await app_ctx.pilot.app.run_action("cut", app_ctx.screen)
    await app_ctx.pilot.pause()

    cb = app_ctx.app._path_clipboard
    assert not cb.empty()
    paths, op = cb.get()
    assert op == ClipboardOperation.CUT
    assert paths[0].name == "file.txt"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cut_on_parent_entry_does_nothing(app_ctx: AppCtx) -> None:
    """_action_cut is a no-op when the cursor is on the '..' entry."""
    from nova_navigator.vfs import VPath

    (app_ctx.src_dir / "file.txt").write_text("")
    app_ctx.screen._left_panel.set_path(VPath(app_ctx.src_dir, app_ctx.fs))
    app_ctx.screen._right_panel.set_path(VPath(app_ctx.dst_dir, app_ctx.fs))
    app_ctx.screen._left_panel.focus()
    await app_ctx.pilot.pause()

    await app_ctx.pilot.app.run_action("cut", app_ctx.screen)
    await app_ctx.pilot.pause()

    assert app_ctx.app._path_clipboard.empty()


# ---------------------------------------------------------------------------
# Paste
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paste_copy_copies_file_to_active_panel(app_ctx: AppCtx) -> None:
    """Paste after Copy calls copy_or_move_files_job with move=False."""
    (app_ctx.src_dir / "file.txt").write_text("data")
    await set_panels(app_ctx)

    # Copy the file into clipboard
    await app_ctx.pilot.app.run_action("copy", app_ctx.screen)
    await app_ctx.pilot.pause()

    # Switch focus to the right panel (destination) and paste
    app_ctx.screen._right_panel.focus()
    await app_ctx.pilot.pause()

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await app_ctx.pilot.app.run_action("paste", app_ctx.screen)
        await poll_until(app_ctx.pilot, lambda: (app_ctx.dst_dir / "file.txt").exists())

    assert (app_ctx.dst_dir / "file.txt").exists()
    # Clipboard preserved after copy-paste
    assert not app_ctx.app._path_clipboard.empty()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paste_cut_moves_file_and_clears_clipboard(app_ctx: AppCtx) -> None:
    """Paste after Cut calls copy_or_move_files_job with move=True and clears clipboard."""
    (app_ctx.src_dir / "file.txt").write_text("data")
    await set_panels(app_ctx)

    # Cut the file into clipboard
    await app_ctx.pilot.app.run_action("cut", app_ctx.screen)
    await app_ctx.pilot.pause()

    # Switch focus to the right panel (destination) and paste
    app_ctx.screen._right_panel.focus()
    await app_ctx.pilot.pause()

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await app_ctx.pilot.app.run_action("paste", app_ctx.screen)
        await poll_until(app_ctx.pilot, lambda: (app_ctx.dst_dir / "file.txt").exists())

    assert (app_ctx.dst_dir / "file.txt").exists()
    assert not (app_ctx.src_dir / "file.txt").exists()
    # Clipboard cleared after cut-paste
    assert app_ctx.app._path_clipboard.empty()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paste_cancel_preserves_clipboard(app_ctx: AppCtx) -> None:
    """Cancelling the Paste dialog leaves the clipboard unchanged."""
    (app_ctx.src_dir / "file.txt").write_text("data")
    await set_panels(app_ctx)

    await app_ctx.pilot.app.run_action("cut", app_ctx.screen)
    await app_ctx.pilot.pause()

    with patch(_COPY_DIALOG_PATH, return_value=auto_cancel_dialog()):
        await app_ctx.pilot.app.run_action("paste", app_ctx.screen)
        await app_ctx.pilot.pause()

    # Clipboard NOT cleared on cancel
    assert not app_ctx.app._path_clipboard.empty()
    # File not moved
    assert (app_ctx.src_dir / "file.txt").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paste_does_nothing_when_clipboard_empty(app_ctx: AppCtx) -> None:
    """_action_paste is a no-op when the clipboard is empty."""
    (app_ctx.src_dir / "file.txt").write_text("data")
    await set_panels(app_ctx)

    dialog_created = False

    def _track(_: object, **__: object) -> object:
        nonlocal dialog_created
        dialog_created = True
        return auto_cancel_dialog()

    with patch(_COPY_DIALOG_PATH, side_effect=_track):
        await app_ctx.pilot.app.run_action("paste", app_ctx.screen)
        await app_ctx.pilot.pause()

    assert not dialog_created
