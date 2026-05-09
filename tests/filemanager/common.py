import asyncio
import threading
from collections.abc import Awaitable, Callable

from nova_navigator.response import Response
from nova_navigator.scheduler import AsyncTaskScheduler, ResponseRequest, TaskContext, TaskStatus
from tests._utils.mock_filesystem import MockFilesystem


def make_status(
    print_progress: bool = False,
    cancel_event: threading.Event | None = None,
) -> TaskStatus:
    def progress_callback(status: TaskStatus) -> None:
        print(
            f"Progress: {status.progress.completed}/{status.progress.total} "
            f"(step {status.progress.step_completed}/{status.progress.step_total})"
        )

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
                f"Task yielded an unexpected ResponseRequest {request.title!r} but no responses remain in the list"
            )
        future.set_result(pending.pop(0))

    await AsyncTaskScheduler.execute(gui_callback, task_fn, status)
    return requests
