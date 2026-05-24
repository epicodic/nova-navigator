"""Integration tests for File > Rename action."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.integration.conftest import (
    AppCtx,
    auto_cancel_dialog,
    auto_confirm_copy_dialog,
    poll_until,
    set_panels,
)

_COPY_DIALOG_PATH = "nova_navigator.filemanager.jobs.CopyMoveFilesDialog"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rename_renames_file_within_same_directory(app_ctx: AppCtx) -> None:
    """Rename moves the file to a new name in the same directory."""
    (app_ctx.src_dir / "original.txt").write_text("hello")
    await set_panels(app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog(filename="renamed.txt")):
        await app_ctx.pilot.app.run_action("rename", app_ctx.screen)
        await poll_until(app_ctx.pilot, lambda: (app_ctx.src_dir / "renamed.txt").exists())

    assert (app_ctx.src_dir / "renamed.txt").exists()
    assert (app_ctx.src_dir / "renamed.txt").read_text() == "hello"
    assert not (app_ctx.src_dir / "original.txt").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rename_cancel_leaves_file_unchanged(app_ctx: AppCtx) -> None:
    """Cancelling the rename dialog leaves the file untouched."""
    (app_ctx.src_dir / "keep.txt").write_text("data")
    await set_panels(app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_cancel_dialog()):
        await app_ctx.pilot.app.run_action("rename", app_ctx.screen)
        await app_ctx.pilot.pause()

    assert (app_ctx.src_dir / "keep.txt").exists()
    assert (app_ctx.src_dir / "keep.txt").read_text() == "data"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rename_on_parent_entry_does_nothing(app_ctx: AppCtx) -> None:
    """When the cursor is on the '..' entry, rename is a no-op."""
    (app_ctx.src_dir / "file.txt").write_text("")
    # Set panels but do NOT press down, so cursor stays on '..'
    app_ctx.screen._left_panel.set_path(__import__("nova_navigator.vfs", fromlist=["VPath"]).VPath(app_ctx.src_dir, app_ctx.fs))
    app_ctx.screen._right_panel.set_path(__import__("nova_navigator.vfs", fromlist=["VPath"]).VPath(app_ctx.dst_dir, app_ctx.fs))
    app_ctx.screen._left_panel.focus()
    await app_ctx.pilot.pause()
    # cursor is on '..' — rename should be a no-op (no dialog shown)

    dialog_created = False

    def _track_dialog(*_args: object, **_kwargs: object) -> object:
        nonlocal dialog_created
        dialog_created = True
        return auto_cancel_dialog()

    with patch(_COPY_DIALOG_PATH, side_effect=_track_dialog):
        await app_ctx.pilot.app.run_action("rename", app_ctx.screen)
        await app_ctx.pilot.pause()

    assert not dialog_created
    assert (app_ctx.src_dir / "file.txt").exists()
