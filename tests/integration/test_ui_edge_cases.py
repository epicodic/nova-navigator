"""Integration tests for UI edge cases in Nova Navigator."""

from __future__ import annotations

import pytest
from textual.screen import ModalScreen

from tests.integration.conftest import AppCtx, set_panels

# ---------------------------------------------------------------------------
# F4 on a directory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f4_on_directory_shows_error_dialog(app_ctx: AppCtx) -> None:
    """Pressing F4 (Edit) on a directory shows an error dialog instead of crashing.

    Editor.open() raises IsADirectoryError for directories; the global exception
    handler must catch it and display a MessageBox rather than terminating.
    """
    subdir = app_ctx.src_dir / "subdir"
    subdir.mkdir()
    await set_panels(app_ctx)  # cursor lands on "subdir"

    await app_ctx.pilot.press("f4")
    await app_ctx.pilot.pause(delay=0.2)

    # An error dialog (ModalScreen) should now be on top of the screen stack.
    assert any(isinstance(s, ModalScreen) for s in app_ctx.pilot.app.screen_stack)

    # Dismiss the error dialog (Continue).  The Editor is popped before the
    # dialog appears, so only MainScreen remains afterwards.
    await app_ctx.pilot.press("escape")
    await app_ctx.pilot.pause()
