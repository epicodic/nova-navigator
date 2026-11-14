"""Integration tests for file deletion (F8) through the full Nova Navigator UI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nova_navigator.config.global_config import conf_
from nova_navigator.dialogs.response_dialog import ResponseDialog
from tests.integration.conftest import (
    AppCtx,
    auto_cancel_dialog,
    auto_confirm_delete_dialog,
    poll_until,
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
        await poll_until(app_ctx.pilot, lambda: not (app_ctx.src_dir / "remove_me.txt").exists())

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
        await poll_until(app_ctx.pilot, lambda: not any((app_ctx.src_dir / n).exists() for n in names))

    for name in names:
        assert not (app_ctx.src_dir / name).exists(), f"{name} still exists after delete"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_without_confirmation_removes_file_without_dialog(app_ctx: AppCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    """With general.confirm_delete off, F8 deletes without the Delete dialog."""
    monkeypatch.setattr(conf_.settings.general, "confirm_delete", False)
    (app_ctx.src_dir / "remove_me.txt").write_text("bye")
    await set_panels(app_ctx)

    with patch(_DELETE_DIALOG_PATH, MagicMock()) as dialog_cls:
        await app_ctx.pilot.press("f8")
        await poll_until(app_ctx.pilot, lambda: not (app_ctx.src_dir / "remove_me.txt").exists())

    dialog_cls.assert_not_called()
    assert not (app_ctx.src_dir / "remove_me.txt").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_without_confirmation_still_asks_for_non_empty_directory(app_ctx: AppCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    """With general.confirm_delete off, a non-empty directory is still confirmed by the erase task."""
    monkeypatch.setattr(conf_.settings.general, "confirm_delete", False)
    full = app_ctx.src_dir / "full"
    full.mkdir()
    (full / "inner.txt").write_text("x")
    await set_panels(app_ctx)

    with patch(_DELETE_DIALOG_PATH, MagicMock()) as dialog_cls:
        await app_ctx.pilot.press("f8")
        await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, ResponseDialog))
        dialog_cls.assert_not_called()
        assert full.exists()

        await app_ctx.pilot.click("#NO")
        await app_ctx.pilot.pause(delay=0.2)

    assert full.exists()
    assert (full / "inner.txt").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_with_confirmation_default_shows_dialog(app_ctx: AppCtx) -> None:
    """By default F8 shows the Delete dialog."""
    (app_ctx.src_dir / "keep_me.txt").write_text("staying")
    await set_panels(app_ctx)

    with patch(_DELETE_DIALOG_PATH, return_value=auto_cancel_dialog()) as dialog_cls:
        await app_ctx.pilot.press("f8")
        await app_ctx.pilot.pause(delay=0.2)

    dialog_cls.assert_called_once()
    assert (app_ctx.src_dir / "keep_me.txt").exists()
