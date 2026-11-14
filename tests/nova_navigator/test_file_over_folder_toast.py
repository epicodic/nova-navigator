"""A real copy or move job with a file that meets a same-named folder produces exactly one toast (REQ-32)."""

import asyncio
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, copy_files, move_files
from nova_navigator.nova_navigator import NovaNavigator
from nova_navigator.scheduler import Job, ResponseRequest
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_widgets import Response


async def _no_gui(_request: ResponseRequest, future: asyncio.Future[Response]) -> None:
    future.set_result(Response.YES)


def _fake_app() -> MagicMock:
    app = MagicMock()
    app.request_callback = _no_gui
    app._notify_skipped = partial(NovaNavigator._notify_skipped, app)
    return app


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (dst / "x").mkdir(parents=True)
    (src / "x").write_text("source x")
    (src / "y").write_text("source y")
    (dst / "x" / "keep.txt").write_text("keep")
    return src, dst


@pytest.mark.asyncio
async def test_copy_job_with_file_over_folder_notifies_once(tmp_path: Path) -> None:
    src, dst = _tree(tmp_path)
    fs = LocalFilesystem()
    app = _fake_app()
    options = FileCopyOptions(overwrite="ask")
    job = Job("Copy", lambda ctx: copy_files(ctx, [fs.path(src / "x"), fs.path(src / "y")], fs.path(dst), options))
    await NovaNavigator.start_job(app, job)
    assert app.notify.call_count == 1
    assert app.notify.call_args.args[0] == "Skipped 1 item: folder exists (1)"
    assert [(skipped.item, skipped.reason) for skipped in job.skipped] == [(fs.path(src / "x").uri, "folder exists")]
    assert (dst / "y").read_text() == "source y"
    assert [path.name for path in (dst / "x").iterdir()] == ["keep.txt"]
    assert (dst / "x" / "keep.txt").read_text() == "keep"


@pytest.mark.asyncio
async def test_move_job_with_file_over_folder_notifies_once_and_keeps_source(tmp_path: Path) -> None:
    src, dst = _tree(tmp_path)
    fs = LocalFilesystem()
    app = _fake_app()
    options = FileCopyOptions(overwrite="ask")
    job = Job("Move", lambda ctx: move_files(ctx, [fs.path(src / "x"), fs.path(src / "y")], fs.path(dst), options))
    await NovaNavigator.start_job(app, job)
    assert app.notify.call_count == 1
    assert app.notify.call_args.args[0] == "Skipped 1 item: folder exists (1)"
    assert [(skipped.item, skipped.reason) for skipped in job.skipped] == [(fs.path(src / "x").uri, "folder exists")]
    assert (src / "x").read_text() == "source x"
    assert not (src / "y").exists()
    assert (dst / "y").read_text() == "source y"
    assert [path.name for path in (dst / "x").iterdir()] == ["keep.txt"]
