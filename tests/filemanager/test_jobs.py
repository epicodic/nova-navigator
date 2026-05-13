"""Tests for filemanager/jobs.py: copy_or_move_files_job and delete_files_job."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_navigator.filemanager.jobs import copy_or_move_files_job, delete_files_job
from nova_navigator.scheduler import Job
from nova_navigator.vfs import VPath

# ── helpers ───────────────────────────────────────────────────────────────────


def _vpath(name: str) -> MagicMock:
    vp = MagicMock(spec=VPath)
    vp.name = name
    vp.__truediv__ = MagicMock(side_effect=lambda _: vp)
    return vp


def _mock_copy_dialog(result: str, filename: str | None = None) -> MagicMock:
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=result)
    dialog.filename = filename
    return dialog


def _mock_delete_dialog(result: str) -> MagicMock:
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=result)
    return dialog


# ── copy_or_move_files_job ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_copy_job_returns_none_when_user_cancels() -> None:
    src = [_vpath("file.txt")]
    dst = _vpath("dest")
    with patch("nova_navigator.filemanager.jobs.CopyMoveFilesDialog", return_value=_mock_copy_dialog("CANCEL")):
        result = await copy_or_move_files_job(src, dst, move=False)  # type: ignore
    assert result is None


@pytest.mark.asyncio
async def test_copy_job_returns_copy_job_on_ok() -> None:
    src = [_vpath("a.txt"), _vpath("b.txt")]
    dst = _vpath("dest")
    with patch(
        "nova_navigator.filemanager.jobs.CopyMoveFilesDialog", return_value=_mock_copy_dialog("OK", filename=None)
    ):
        result = await copy_or_move_files_job(src, dst, move=False)  # type: ignore
    assert isinstance(result, Job)
    assert result.title == "Copy Files"


@pytest.mark.asyncio
async def test_move_job_returns_move_job_on_ok() -> None:
    src = [_vpath("a.txt"), _vpath("b.txt")]
    dst = _vpath("dest")
    with patch(
        "nova_navigator.filemanager.jobs.CopyMoveFilesDialog", return_value=_mock_copy_dialog("OK", filename=None)
    ):
        result = await copy_or_move_files_job(src, dst, move=True)  # type: ignore
    assert isinstance(result, Job)
    assert result.title == "Move Files"


@pytest.mark.asyncio
async def test_copy_job_single_file_uses_edited_filename() -> None:
    src = [_vpath("original.txt")]
    dst = _vpath("dest")
    dialog = _mock_copy_dialog("OK", filename="renamed.txt")
    with patch("nova_navigator.filemanager.jobs.CopyMoveFilesDialog", return_value=dialog):
        result = await copy_or_move_files_job(src, dst, move=False)  # type: ignore
    assert result is not None
    # dst / "renamed.txt" should have been called
    dst.__truediv__.assert_called_once_with("renamed.txt")


@pytest.mark.asyncio
async def test_copy_job_single_file_no_rename_when_filename_is_none() -> None:
    src = [_vpath("file.txt")]
    dst = _vpath("dest")
    dialog = _mock_copy_dialog("OK", filename=None)
    with patch("nova_navigator.filemanager.jobs.CopyMoveFilesDialog", return_value=dialog):
        result = await copy_or_move_files_job(src, dst, move=False)  # type: ignore
    assert result is not None
    dst.__truediv__.assert_not_called()


@pytest.mark.asyncio
async def test_move_job_returns_none_when_user_cancels() -> None:
    src = [_vpath("file.txt")]
    dst = _vpath("dest")
    with patch("nova_navigator.filemanager.jobs.CopyMoveFilesDialog", return_value=_mock_copy_dialog("CANCEL")):
        result = await copy_or_move_files_job(src, dst, move=True)  # type: ignore
    assert result is None


# ── delete_files_job ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_job_returns_none_when_user_cancels() -> None:
    paths = [_vpath("file.txt")]
    with patch("nova_navigator.filemanager.jobs.DeleteFilesDialog", return_value=_mock_delete_dialog("NO")):
        result = await delete_files_job(paths)  # type: ignore
    assert result is None


@pytest.mark.asyncio
async def test_delete_job_returns_erase_job_on_yes() -> None:
    paths = [_vpath("file.txt"), _vpath("other.txt")]
    with patch("nova_navigator.filemanager.jobs.DeleteFilesDialog", return_value=_mock_delete_dialog("YES")):
        result = await delete_files_job(paths)  # type: ignore
    assert isinstance(result, Job)
    assert result.title == "Erase Files"
