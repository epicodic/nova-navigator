import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, copy_files, move_files
from nova_navigator.scheduler import AsyncTaskScheduler, ResponseRequest, TaskContext, TaskStatus
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from nova_widgets import Response
from tests._utils.mock_filesystem import MockFilesystem


def make_status(
    print_progress: bool = False,
    cancel_event: threading.Event | None = None,
) -> TaskStatus:
    def progress_callback(status: TaskStatus) -> None:
        print(f"Progress: {status.progress.completed}/{status.progress.total} (step {status.progress.step_completed}/{status.progress.step_total})")

    cb = progress_callback if print_progress else (lambda _: None)
    return TaskStatus(
        cancel_event=cancel_event or threading.Event(),
        progress_callback=cb,
    )


def read_all(fs: MockFilesystem, path: str) -> bytes:
    reader = fs.read(fs.path(path))
    data = reader.read(1024 * 1024)
    reader.close()
    return data


async def run_task(
    task_fn: Callable[[TaskContext], Awaitable[None]],
    responses: list[Response] | None = None,
    status: TaskStatus | None = None,
) -> list[ResponseRequest]:
    """Run *task_fn* via AsyncTaskScheduler with pre-supplied GUI responses.

    Returns the list of ResponseRequests that were presented to the GUI callback.
    Raises any exception the task raises (e.g. TaskCancelled, OSError).
    """
    pending = list(responses or [])
    requests: list[ResponseRequest] = []
    if status is None:
        status = make_status()

    async def gui_callback(request: ResponseRequest, future: asyncio.Future[Response]) -> None:
        requests.append(request)
        if not pending:
            raise AssertionError(
                f"Task yielded an unexpected ResponseRequest {request.title!r} but no responses remain in the list",
            )
        future.set_result(pending.pop(0))

    await AsyncTaskScheduler.execute(gui_callback, task_fn, status)
    return requests


async def copy_between(
    fs_pair: tuple[Filesystem, Filesystem],
    sources: list[Path],
    destination: Path,
    options: FileCopyOptions | None = None,
    responses: list[Response] | None = None,
) -> TaskStatus:
    """Copy *sources* (viewed through ``fs_pair[0]``) into *destination* (viewed through ``fs_pair[1]``)."""
    src_fs, dst_fs = fs_pair
    status = make_status()
    await asyncio.wait_for(
        run_task(
            lambda ctx: copy_files(ctx, [src_fs.path(s) for s in sources], dst_fs.path(destination), options),
            responses,
            status,
        ),
        timeout=20,
    )
    return status


async def move_between(
    fs_pair: tuple[Filesystem, Filesystem],
    sources: list[Path],
    destination: Path,
    options: FileCopyOptions | None = None,
    responses: list[Response] | None = None,
) -> TaskStatus:
    """As ``copy_between`` but runs ``move_files``."""
    src_fs, dst_fs = fs_pair
    status = make_status()
    await asyncio.wait_for(
        run_task(
            lambda ctx: move_files(ctx, [src_fs.path(s) for s in sources], dst_fs.path(destination), options),
            responses,
            status,
        ),
        timeout=20,
    )
    return status


def force_cross_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report "different device" for LocalFilesystem so moves copy instead of renaming (test_move_links.py:36-40)."""

    def is_same_device(_self: LocalFilesystem, _a: VPath, _b: VPath) -> bool:
        return False

    monkeypatch.setattr(LocalFilesystem, "is_same_device", is_same_device)


def reasons(status: TaskStatus) -> list[str]:
    """Return the skip reasons recorded on *status*, in report order."""
    return [skipped.reason for skipped in status.skipped]
