"""Integration tests for File > Copy Names action."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.integration.conftest import AppCtx, set_panels


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_names_copies_cursor_filename_to_clipboard(app_ctx: AppCtx) -> None:
    """Copy Names with no selection copies the filename under the cursor."""
    (app_ctx.src_dir / "hello.txt").write_text("")
    await set_panels(app_ctx)

    with patch.object(app_ctx.pilot.app, "copy_to_clipboard") as mock_clipboard:
        await app_ctx.pilot.app.run_action("copy_names", app_ctx.screen)
        await app_ctx.pilot.pause()

    mock_clipboard.assert_called_once_with("hello.txt")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_names_copies_all_selected_filenames(app_ctx: AppCtx) -> None:
    """Copy Names with multiple selected files copies all names joined by newlines."""
    for name in ["a.txt", "b.txt", "c.txt"]:
        (app_ctx.src_dir / name).write_text("")
    await set_panels(app_ctx)

    await app_ctx.pilot.press("ctrl+a")
    await app_ctx.pilot.pause()

    with patch.object(app_ctx.pilot.app, "copy_to_clipboard") as mock_clipboard:
        await app_ctx.pilot.app.run_action("copy_names", app_ctx.screen)
        await app_ctx.pilot.pause()

    mock_clipboard.assert_called_once()
    copied = mock_clipboard.call_args[0][0]
    assert set(copied.split("\n")) == {"a.txt", "b.txt", "c.txt"}
