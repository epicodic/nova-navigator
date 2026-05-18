"""Integration tests for File > New > File action."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_navigator.response import Response
from tests.integration.conftest import AppCtx, set_panels

_INPUT_NAME_DIALOG_PATH = "nova_navigator.nova_navigator.InputNameDialog"


def _auto_confirm_name_dialog(name: str) -> MagicMock:
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=Response.OK)
    dialog.value = name
    return dialog


def _auto_cancel_name_dialog() -> MagicMock:
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=Response.CANCEL)
    dialog.value = ""
    return dialog


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_file_creates_empty_file_on_confirm(app_ctx: AppCtx) -> None:
    """New > File creates an empty file in the active panel's current path."""
    (app_ctx.src_dir / "existing.txt").write_text("")
    await set_panels(app_ctx)

    with patch(_INPUT_NAME_DIALOG_PATH, return_value=_auto_confirm_name_dialog("newfile.txt")):
        await app_ctx.pilot.app.run_action("new_file", app_ctx.screen)
        await app_ctx.pilot.pause()

    assert (app_ctx.src_dir / "newfile.txt").exists()
    assert (app_ctx.src_dir / "newfile.txt").read_bytes() == b""


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_file_does_nothing_on_cancel(app_ctx: AppCtx) -> None:
    """Cancelling the dialog creates nothing."""
    await set_panels(app_ctx)

    with patch(_INPUT_NAME_DIALOG_PATH, return_value=_auto_cancel_name_dialog()):
        await app_ctx.pilot.app.run_action("new_file", app_ctx.screen)
        await app_ctx.pilot.pause()

    assert not any(app_ctx.src_dir.iterdir())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_file_shows_error_on_oserror(app_ctx: AppCtx) -> None:
    """An OSError from write shows an error dialog."""
    (app_ctx.src_dir / "existing.txt").write_text("")
    await set_panels(app_ctx)

    with (
        patch(_INPUT_NAME_DIALOG_PATH, return_value=_auto_confirm_name_dialog("denied.txt")),
        patch("nova_navigator.nova_navigator.MessageBox") as mock_msgbox,
        patch(
            "nova_navigator.vfs.filesystems.local.LocalFilesystem.write",
            side_effect=PermissionError(13, "Permission denied"),
        ),
    ):
        mock_msgbox.return_value.run = AsyncMock()
        await app_ctx.pilot.app.run_action("new_file", app_ctx.screen)
        await app_ctx.pilot.pause()

    mock_msgbox.assert_called_once()
