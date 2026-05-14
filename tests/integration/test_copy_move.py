"""Integration tests for file copy (F5) through the full Nova Navigator UI.

Each test launches a real NovaNavigator app with a real temporary filesystem.
The CopyMoveFilesDialog is mocked to auto-confirm so tests focus on verifying
that the actual copy operation reaches the filesystem correctly.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nova_navigator.decision import Decision
from tests.integration.conftest import (
    AppCtx,
    auto_cancel_dialog,
    auto_confirm_copy_dialog,
    auto_confirm_decision_dialog,
    poll_until,
    set_panels,
)

_COPY_DIALOG_PATH = "nova_navigator.filemanager.jobs.CopyMoveFilesDialog"


# ---------------------------------------------------------------------------
# Single-file copy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_single_file_appears_in_destination(app_ctx: AppCtx) -> None:
    """F5 on a single file copies it to the right panel directory."""
    (app_ctx.src_dir / "hello.txt").write_text("content")
    await set_panels(app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await app_ctx.pilot.press("f5")
        await poll_until(app_ctx.pilot, lambda: (app_ctx.dst_dir / "hello.txt").exists())

    assert (app_ctx.dst_dir / "hello.txt").exists()
    assert (app_ctx.dst_dir / "hello.txt").read_text() == "content"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_single_file_preserves_content(app_ctx: AppCtx) -> None:
    """The copied file contains byte-identical content to the original."""
    data = b"\x00\x01\x02\xff" * 256
    (app_ctx.src_dir / "binary.bin").write_bytes(data)
    await set_panels(app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await app_ctx.pilot.press("f5")
        await poll_until(app_ctx.pilot, lambda: (app_ctx.dst_dir / "binary.bin").exists())

    assert (app_ctx.dst_dir / "binary.bin").read_bytes() == data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_single_file_with_rename(app_ctx: AppCtx) -> None:
    """When the user edits the filename in the dialog, the copy uses that name."""
    (app_ctx.src_dir / "original.txt").write_text("hello")
    await set_panels(app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog(filename="renamed.txt")):
        await app_ctx.pilot.press("f5")
        await poll_until(app_ctx.pilot, lambda: (app_ctx.dst_dir / "renamed.txt").exists())

    assert (app_ctx.dst_dir / "renamed.txt").exists()
    assert not (app_ctx.dst_dir / "original.txt").exists()


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_cancelled_leaves_destination_empty(app_ctx: AppCtx) -> None:
    """Cancelling the copy dialog leaves the destination directory untouched."""
    (app_ctx.src_dir / "file.txt").write_text("data")
    await set_panels(app_ctx)

    with patch(_COPY_DIALOG_PATH, return_value=auto_cancel_dialog()):
        await app_ctx.pilot.press("f5")
        await app_ctx.pilot.pause(delay=0.2)

    assert list(app_ctx.dst_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Multiple files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_multiple_files_all_appear_in_destination(app_ctx: AppCtx) -> None:
    """F5 copies all selected files to the destination (cursor-item selection)."""
    # Create three files; the browser selects each in turn via space-bar.
    names = ["alpha.txt", "beta.txt", "gamma.txt"]
    for name in names:
        (app_ctx.src_dir / name).write_text(name)

    await set_panels(app_ctx)

    # Select all three files with Ins (action_insert_select moves cursor down automatically).
    for _ in names:
        await app_ctx.pilot.press("insert")
    await app_ctx.pilot.pause()

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await app_ctx.pilot.press("f5")
        await poll_until(app_ctx.pilot, lambda: all(
            (app_ctx.dst_dir / n).exists() for n in names
        ))

    for name in names:
        assert (app_ctx.dst_dir / name).exists(), f"{name} missing from destination"


# ---------------------------------------------------------------------------
# Overwrite — exercises request_callback
# ---------------------------------------------------------------------------

_DECISION_DIALOG_PATH = "nova_navigator.nova_navigator.make_decision_dialog"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_overwrites_existing_file_when_user_confirms(app_ctx: AppCtx) -> None:
    """When the destination file already exists and the user confirms overwrite,
    the source content replaces the destination content.

    This exercises the full request_callback → make_decision_dialog path that
    fires when the copy task detects a conflict.
    """
    (app_ctx.src_dir / "file.txt").write_text("new content")
    (app_ctx.dst_dir / "file.txt").write_text("old content")
    await set_panels(app_ctx)

    p_copy = patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog())
    p_dec = patch(_DECISION_DIALOG_PATH, return_value=auto_confirm_decision_dialog(Decision.YES))
    with p_copy, p_dec:
        await app_ctx.pilot.press("f5")
        await poll_until(app_ctx.pilot,
            lambda: (app_ctx.dst_dir / "file.txt").read_text() == "new content")

    assert (app_ctx.dst_dir / "file.txt").read_text() == "new content"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_skips_existing_file_when_user_declines_overwrite(app_ctx: AppCtx) -> None:
    """When the destination file exists and the user declines, the original is preserved."""
    (app_ctx.src_dir / "file.txt").write_text("new content")
    (app_ctx.dst_dir / "file.txt").write_text("old content")
    await set_panels(app_ctx)

    p_copy = patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog())
    p_dec = patch(_DECISION_DIALOG_PATH, return_value=auto_confirm_decision_dialog(Decision.NO))
    with p_copy, p_dec:
        await app_ctx.pilot.press("f5")
        await app_ctx.pilot.pause(delay=0.5)

    assert (app_ctx.dst_dir / "file.txt").read_text() == "old content"
