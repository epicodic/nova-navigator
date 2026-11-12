"""A real copy job with three skipped links produces exactly one toast naming the count and the word link (REQ-6/REQ-7)."""

import asyncio
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nova_navigator.filemanager.tasks import copy_files
from nova_navigator.nova_navigator import NovaNavigator
from nova_navigator.scheduler import Job, ResponseRequest
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_widgets import Response
from tests._utils.mock_filesystem import MockFilesystem


async def _no_gui(_request: ResponseRequest, future: asyncio.Future[Response]) -> None:
    future.set_result(Response.YES)


def _fake_app() -> MagicMock:
    app = MagicMock()
    app.request_callback = _no_gui
    app._notify_skipped = partial(NovaNavigator._notify_skipped, app)
    return app


def _tree(tmp_path: Path, broken: int) -> Path:
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "d" / "up").symlink_to("..", target_is_directory=True)
    (src / "ok.txt").write_text("ok")
    for index in range(broken):
        (src / f"b{index}").symlink_to("nope")
    return src


@pytest.mark.asyncio
async def test_copy_job_with_three_skipped_links_notifies_once(tmp_path: Path) -> None:
    src = _tree(tmp_path, broken=2)
    dst = MockFilesystem(symlinks=False)
    app = _fake_app()
    job = Job("Copy", lambda ctx: copy_files(ctx, [LocalFilesystem().path(src)], dst.path("/home/user")))
    await NovaNavigator.start_job(app, job)
    assert app.notify.call_count == 1
    text = app.notify.call_args.args[0]
    assert "3" in text
    assert "link" in text
    assert [skipped.reason for skipped in job.skipped].count("broken link") == 2
    assert [skipped.reason for skipped in job.skipped].count("link loop") == 1
    assert {skipped.item for skipped in job.skipped} >= {LocalFilesystem().path(src / "b0").uri, LocalFilesystem().path(src / "b1").uri}


@pytest.mark.asyncio
async def test_copy_job_without_skips_does_not_notify(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.txt").write_text("ok")
    dst = MockFilesystem(symlinks=False)
    app = _fake_app()
    job = Job("Copy", lambda ctx: copy_files(ctx, [LocalFilesystem().path(src)], dst.path("/home/user")))
    await NovaNavigator.start_job(app, job)
    assert app.notify.call_count == 0
