"""Tests for filemanager/jobs.py: copy_or_move_files_job and delete_files_job."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_navigator.config import loader
from nova_navigator.config.global_config import conf_
from nova_navigator.config.settings import Settings
from nova_navigator.filemanager.jobs import copy_or_move_files_job, delete_files_job
from nova_navigator.filemanager.tasks import FileCopyOptions, erase_files
from nova_navigator.scheduler import Job
from nova_navigator.vfs import VPath
from nova_widgets import Response

# ── helpers ───────────────────────────────────────────────────────────────────


def _vpath(name: str) -> MagicMock:
    vp = MagicMock(spec=VPath)
    vp.name = name
    vp.__truediv__ = MagicMock(side_effect=lambda _: vp)
    return vp


def _mock_copy_dialog(
    result: Response,
    filename: str | None = None,
    follow_links: bool = False,
    stable_symlinks: bool = False,
) -> MagicMock:
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=result)
    dialog.filename = filename
    dialog.file_filter = None
    dialog.follow_links = follow_links
    dialog.stable_symlinks = stable_symlinks
    return dialog


def _mock_delete_dialog(result: Response) -> MagicMock:
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=result)
    return dialog


@pytest.fixture(autouse=True)
def _settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give `conf_` a Settings object backed by a temp config dir, since jobs.py reads and saves it."""
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)
    settings = Settings.load()
    monkeypatch.setattr(conf_, "settings", settings, raising=False)


async def _run_job(move: bool, dialog: MagicMock) -> tuple[Job | None, MagicMock, MagicMock]:
    """Run copy_or_move_files_job with a patched dialog and Job; return (result, dialog_cls, job_cls)."""
    with (
        patch("nova_navigator.filemanager.jobs.CopyMoveFilesDialog", return_value=dialog) as dialog_cls,
        patch("nova_navigator.filemanager.jobs.Job") as job_cls,
    ):
        src = cast("list[VPath]", [_vpath("a.txt")])
        dst = cast("VPath", _vpath("dest"))
        result = await copy_or_move_files_job(src, dst, move=move)
    return result, dialog_cls, job_cls


# ── copy_or_move_files_job ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_copy_job_returns_copy_job_on_ok() -> None:
    src = [_vpath("a.txt"), _vpath("b.txt")]
    dst = _vpath("dest")
    with patch(
        "nova_navigator.filemanager.jobs.CopyMoveFilesDialog",
        return_value=_mock_copy_dialog(Response.OK, filename=None),
    ):
        result = await copy_or_move_files_job(src, dst, move=False)  # type: ignore
    assert isinstance(result, Job)
    assert result.title == "Copy Files"


@pytest.mark.asyncio
async def test_move_job_returns_move_job_on_ok() -> None:
    src = [_vpath("a.txt"), _vpath("b.txt")]
    dst = _vpath("dest")
    with patch(
        "nova_navigator.filemanager.jobs.CopyMoveFilesDialog",
        return_value=_mock_copy_dialog(Response.OK, filename=None),
    ):
        result = await copy_or_move_files_job(src, dst, move=True)  # type: ignore
    assert isinstance(result, Job)
    assert result.title == "Move Files"


@pytest.mark.asyncio
async def test_copy_job_single_file_uses_edited_filename() -> None:
    src = [_vpath("original.txt")]
    dst = _vpath("dest")
    dialog = _mock_copy_dialog(Response.OK, filename="renamed.txt")
    with patch("nova_navigator.filemanager.jobs.CopyMoveFilesDialog", return_value=dialog):
        result = await copy_or_move_files_job(src, dst, move=False)  # type: ignore
    assert result is not None
    # dst / "renamed.txt" should have been called
    dst.__truediv__.assert_called_once_with("renamed.txt")


@pytest.mark.asyncio
async def test_copy_job_single_file_no_rename_when_filename_is_none() -> None:
    src = [_vpath("file.txt")]
    dst = _vpath("dest")
    dialog = _mock_copy_dialog(Response.OK, filename=None)
    with patch("nova_navigator.filemanager.jobs.CopyMoveFilesDialog", return_value=dialog):
        result = await copy_or_move_files_job(src, dst, move=False)  # type: ignore
    assert result is not None
    dst.__truediv__.assert_not_called()


# ── link options ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dialog_opens_off_without_saved_state() -> None:
    _, dialog_cls, _ = await _run_job(False, _mock_copy_dialog(Response.CANCEL))
    kwargs = dialog_cls.call_args.kwargs
    assert kwargs["follow_links"] is False
    assert kwargs["stable_symlinks"] is False


@pytest.mark.asyncio
async def test_ok_with_both_off_builds_default_link_options() -> None:
    _, _, job_cls = await _run_job(False, _mock_copy_dialog(Response.OK))
    options = job_cls.call_args.args[4]
    assert isinstance(options, FileCopyOptions)
    assert options.follow_links is False
    assert options.stable_symlinks is False


@pytest.mark.asyncio
@pytest.mark.parametrize("move", [False, True])
async def test_ok_passes_chosen_link_options_to_job(move: bool) -> None:
    dialog = _mock_copy_dialog(Response.OK, follow_links=True, stable_symlinks=True)
    result, _, job_cls = await _run_job(move, dialog)
    assert result is job_cls.return_value
    assert job_cls.call_args.args[0] == ("Move Files" if move else "Copy Files")
    options = job_cls.call_args.args[4]
    assert options.follow_links is True
    assert options.stable_symlinks is True


@pytest.mark.asyncio
async def test_ok_saves_chosen_states_to_settings_file() -> None:
    await _run_job(False, _mock_copy_dialog(Response.OK, follow_links=True, stable_symlinks=False))
    reloaded = Settings.load()
    assert reloaded.copy_move.follow_links is True
    assert reloaded.copy_move.stable_symlinks is False


@pytest.mark.asyncio
@pytest.mark.parametrize("move", [False, True])
async def test_cancel_starts_no_job_and_saves_nothing(move: bool, tmp_path: Path) -> None:
    before = (tmp_path / "settings.toml").read_text()
    dialog = _mock_copy_dialog(Response.CANCEL, follow_links=True, stable_symlinks=True)
    result, _, job_cls = await _run_job(move, dialog)
    assert result is None
    job_cls.assert_not_called()
    assert (tmp_path / "settings.toml").read_text() == before
    assert conf_.settings.copy_move.follow_links is False
    assert conf_.settings.copy_move.stable_symlinks is False


@pytest.mark.asyncio
async def test_copy_and_move_share_one_saved_pair() -> None:
    await _run_job(False, _mock_copy_dialog(Response.OK, follow_links=True, stable_symlinks=False))
    _, dialog_cls, _ = await _run_job(True, _mock_copy_dialog(Response.CANCEL))
    kwargs = dialog_cls.call_args.kwargs
    assert kwargs["move"] is True
    assert kwargs["follow_links"] is True
    assert kwargs["stable_symlinks"] is False


@pytest.mark.asyncio
async def test_saved_states_reach_dialog_after_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    await _run_job(False, _mock_copy_dialog(Response.OK, follow_links=False, stable_symlinks=True))
    monkeypatch.setattr(conf_, "settings", Settings.load(), raising=False)  # simulates a restart
    _, dialog_cls, _ = await _run_job(False, _mock_copy_dialog(Response.CANCEL))
    kwargs = dialog_cls.call_args.kwargs
    assert kwargs["follow_links"] is False
    assert kwargs["stable_symlinks"] is True


@pytest.mark.asyncio
async def test_save_failure_still_returns_job(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _fail(_self: Settings) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Settings, "save", _fail)
    with caplog.at_level(logging.WARNING, logger="nova_navigator.filemanager.jobs"):
        result, _, job_cls = await _run_job(False, _mock_copy_dialog(Response.OK, follow_links=True))
    assert result is job_cls.return_value
    assert any(r.levelno == logging.WARNING and "copy/move settings" in r.getMessage() for r in caplog.records)
    assert conf_.settings.copy_move.follow_links is True


# ── delete_files_job ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_job_returns_none_when_user_cancels() -> None:
    paths = [_vpath("file.txt")]
    with patch("nova_navigator.filemanager.jobs.DeleteFilesDialog", return_value=_mock_delete_dialog(Response.NO)):
        result = await delete_files_job(paths)  # type: ignore
    assert result is None


@pytest.mark.asyncio
async def test_delete_job_returns_erase_job_on_yes() -> None:
    paths = [_vpath("file.txt"), _vpath("other.txt")]
    with patch("nova_navigator.filemanager.jobs.DeleteFilesDialog", return_value=_mock_delete_dialog(Response.YES)):
        result = await delete_files_job(paths)  # type: ignore
    assert isinstance(result, Job)
    assert result.title == "Erase Files"


@pytest.mark.asyncio
async def test_delete_job_without_confirmation_skips_dialog_and_returns_erase_job() -> None:
    conf_.settings.general.confirm_delete = False
    paths = cast("list[VPath]", [_vpath("file.txt")])
    with patch("nova_navigator.filemanager.jobs.DeleteFilesDialog") as dialog_cls:
        result = await delete_files_job(paths)
    dialog_cls.assert_not_called()
    assert isinstance(result, Job)
    assert result.title == "Erase Files"


@pytest.mark.asyncio
async def test_delete_job_without_confirmation_keeps_non_empty_directory_prompt() -> None:
    conf_.settings.general.confirm_delete = False
    paths = cast("list[VPath]", [_vpath("dir")])
    with patch("nova_navigator.filemanager.jobs.Job") as job_cls:
        await delete_files_job(paths)
    assert job_cls.call_args.args[:2] == ("Erase Files", erase_files)
    assert len(job_cls.call_args.args) == 3


@pytest.mark.asyncio
async def test_delete_job_reads_option_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = cast("list[VPath]", [_vpath("file.txt")])
    with patch("nova_navigator.filemanager.jobs.DeleteFilesDialog", return_value=_mock_delete_dialog(Response.NO)) as dialog_cls:
        assert await delete_files_job(paths) is None
        dialog_cls.assert_called_once()

        replaced = Settings.load()
        replaced.general.confirm_delete = False
        monkeypatch.setattr(conf_, "settings", replaced, raising=False)
        result = await delete_files_job(paths)
        dialog_cls.assert_called_once()
    assert isinstance(result, Job)
