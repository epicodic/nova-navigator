"""Integration tests for File > New > Directory action."""

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
async def test_new_directory_creates_directory_on_confirm(app_ctx: AppCtx) -> None:
    """New > Directory creates the directory in the active panel's current path."""
    (app_ctx.src_dir / "existing.txt").write_text("")
    await set_panels(app_ctx)

    with patch(_INPUT_NAME_DIALOG_PATH, return_value=_auto_confirm_name_dialog("mydir")):
        await app_ctx.pilot.app.run_action("new_directory", app_ctx.screen)
        await app_ctx.pilot.pause()

    assert (app_ctx.src_dir / "mydir").is_dir()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_directory_does_nothing_on_cancel(app_ctx: AppCtx) -> None:
    """Cancelling the dialog creates nothing."""
    await set_panels(app_ctx)

    with patch(_INPUT_NAME_DIALOG_PATH, return_value=_auto_cancel_name_dialog()):
        await app_ctx.pilot.app.run_action("new_directory", app_ctx.screen)
        await app_ctx.pilot.pause()

    assert not any(app_ctx.src_dir.iterdir())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_directory_shows_error_when_already_exists(app_ctx: AppCtx) -> None:
    """Trying to create a directory that already exists shows an error notification."""
    (app_ctx.src_dir / "clash").mkdir()
    (app_ctx.src_dir / "existing.txt").write_text("")
    await set_panels(app_ctx)

    with (
        patch(_INPUT_NAME_DIALOG_PATH, return_value=_auto_confirm_name_dialog("clash")),
        patch.object(app_ctx.screen, "notify") as mock_notify,
    ):
        await app_ctx.pilot.app.run_action("new_directory", app_ctx.screen)
        await app_ctx.pilot.pause()

    mock_notify.assert_called_once()
    assert mock_notify.call_args.kwargs["severity"] == "error"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_directory_shows_error_on_permission_denied(app_ctx: AppCtx) -> None:
    """A PermissionError (or any OSError) from mkdir shows an error notification."""
    (app_ctx.src_dir / "existing.txt").write_text("")
    await set_panels(app_ctx)

    with (
        patch(_INPUT_NAME_DIALOG_PATH, return_value=_auto_confirm_name_dialog("denied")),
        patch.object(app_ctx.screen, "notify") as mock_notify,
        patch(
            "nova_navigator.vfs.filesystems.local.LocalFilesystem.mkdir",
            side_effect=PermissionError(13, "Permission denied"),
        ),
    ):
        await app_ctx.pilot.app.run_action("new_directory", app_ctx.screen)
        await app_ctx.pilot.pause()

    mock_notify.assert_called_once()
    assert mock_notify.call_args.kwargs["severity"] == "error"
