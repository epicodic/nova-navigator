"""Integration tests for file deletion (F8) through the full Nova Navigator UI."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.integration.conftest import (
    AppCtx,
    auto_cancel_dialog,
    auto_confirm_delete_dialog,
    set_panels,
)

_DELETE_DIALOG_PATH = "nova_navigator.filemanager.jobs.DeleteFilesDialog"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_single_file_removes_it_from_filesystem(app_ctx: AppCtx) -> None:
    """F8 on a single file deletes it after the user confirms."""
    (app_ctx.src_dir / "remove_me.txt").write_text("bye")
    await set_panels(app_ctx)

    with patch(_DELETE_DIALOG_PATH, return_value=auto_confirm_delete_dialog()):
        await app_ctx.pilot.press("f8")
        await app_ctx.pilot.pause(delay=0.5)

    assert not (app_ctx.src_dir / "remove_me.txt").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_cancelled_leaves_file_intact(app_ctx: AppCtx) -> None:
    """Cancelling the delete dialog leaves the file untouched."""
    (app_ctx.src_dir / "keep_me.txt").write_text("staying")
    await set_panels(app_ctx)

    with patch(_DELETE_DIALOG_PATH, return_value=auto_cancel_dialog()):
        await app_ctx.pilot.press("f8")
        await app_ctx.pilot.pause(delay=0.2)

    assert (app_ctx.src_dir / "keep_me.txt").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_multiple_files_all_removed(app_ctx: AppCtx) -> None:
    """Selecting multiple files and pressing F8 removes all of them."""
    names = ["a.txt", "b.txt", "c.txt"]
    for name in names:
        (app_ctx.src_dir / name).write_text(name)
    await set_panels(app_ctx)

    for _ in names:
        await app_ctx.pilot.press("insert")
    await app_ctx.pilot.pause()

    with patch(_DELETE_DIALOG_PATH, return_value=auto_confirm_delete_dialog()):
        await app_ctx.pilot.press("f8")
        await app_ctx.pilot.pause(delay=0.5)

    for name in names:
        assert not (app_ctx.src_dir / name).exists(), f"{name} still exists after delete"
