import asyncio
import threading
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from nova_navigator.response import Response


@dataclass(init=False)
class ResponseRequest:
    """A request for a user response, yielded by a task.

    The *title* also serves as the deduplication key inside :class:`TaskScheduler` so
    that ``ALL`` / ``NO`` responses suppress identical prompts.

    *dialog_type* is an optional string tag used by the GUI to select a specialised
    dialog widget (e.g. ``"overwrite"`` renders :class:`OverwriteResponseDialog`).

    *details* is a free-form dict that the specialised dialog can inspect to render
    additional context (filenames, sizes, dates, …).
    """

    title: str
    expected_responses: list[Response]
    message: str
    dialog_type: str | None
    details: dict[str, Any]

    def __init__(
        self,
        title: str,
        expected_responses: list[Response],
        message: str,
        dialog_type: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.title = title
        self.expected_responses = expected_responses
        self.message = message
        self.dialog_type = dialog_type
        self.details = details if details is not None else {}


@dataclass
class Progress:
    """A snapshot of a task's overall and per-step progress counters.

    *completed* / *total* track the number of high-level items processed
    (e.g. files); *step_completed* / *step_total* track finer-grained progress
    within the current item (e.g. bytes transferred).
    """

    completed: int = 0
    total: int = 0
    step_total: int = 0
    step_completed: int = 0


class TaskCancelled(Exception):
    """Raised when a task is cancelled by the user."""


class TaskStatus:
    """Thread-safe task state holder shared between a worker thread and the GUI.

    Worker code calls :meth:`update_progress`, :meth:`set_step_progress`, etc.
    to report progress; each call invokes the *progress_callback* so the GUI
    can refresh.  :meth:`check_cancelled` raises :exc:`TaskCancelled` when the
    cancel event has been set.
    """

    ProgressUpdateCallback = Callable[["TaskStatus"], None]

    _cancel_event: threading.Event
    _progress_callback: ProgressUpdateCallback
    _progress: Progress

    def __init__(
        self,
        cancel_event: threading.Event,
        progress_callback: ProgressUpdateCallback,
    ) -> None:
        self._cancel_event = cancel_event
        self._progress_callback = progress_callback
        self._progress = Progress()

    @property
    def progress(self) -> Progress:
        return self._progress

    @property
    def cancel_event(self) -> threading.Event | None:
        return self._cancel_event

    @property
    def progress_callback(self) -> ProgressUpdateCallback:
        return self._progress_callback

    def check_cancelled(self) -> None:
        """Raise :exc:`TaskCancelled` if the cancel event has been set."""
        if self._cancel_event.is_set():
            raise TaskCancelled

    def update_progress(self, inc_completed: int = 0, inc_total: int = 0) -> None:
        """Increment the overall completed and/or total counters by the given deltas."""
        self._progress.total += inc_total
        self._progress.completed += inc_completed
        self._progress_callback(self)

    def set_progress(self, completed: int, total: int) -> None:
        """Set the overall completed and total counters to absolute values."""
        self._progress.completed = completed
        self._progress.total = total
        self._progress_callback(self)

    def set_completed(self) -> None:
        """Mark the task as fully complete by setting completed equal to total."""
        self.set_progress(self._progress.total, self._progress.total)

    def update_step_progress(self, inc_completed: int = 0, inc_total: int = 0) -> None:
        """Increment the per-step completed and/or total counters by the given deltas."""
        self._progress.step_total += inc_total
        self._progress.step_completed += inc_completed
        self._progress_callback(self)

    def set_step_progress(self, completed: int, total: int) -> None:
        """Set the per-step completed and total counters to absolute values."""
        self._progress.step_completed = completed
        self._progress.step_total = total
        self._progress_callback(self)

    def set_step_completed(self) -> None:
        """Mark the current step as fully complete."""
        self.set_step_progress(self._progress.step_total, self._progress.step_total)

    def is_complete(self) -> bool:
        """Return ``True`` if the task is fully complete (completed >= total)."""
        return (
            self._progress.completed >= self._progress.total
            and self._progress.step_completed >= self._progress.step_total
        )


GuiRequestCallback = Callable[[ResponseRequest, asyncio.Future[Response]], Awaitable[None]]


class _SubtaskTracker:
    """Reference-counts in-flight subtasks so the worker loop can drain after the root task returns.

    task_started() and the done callback are both called from the same worker-thread
    event loop, so no locking is needed.
    """

    _count: int
    _done_event: asyncio.Event
    _first_exc: BaseException | None

    def __init__(self) -> None:
        self._count = 0
        self._done_event = asyncio.Event()
        self._done_event.set()
        self._first_exc = None

    def task_started(self) -> None:
        if self._count == 0:
            self._done_event.clear()
        self._count += 1

    def task_finished(self, task: asyncio.Task[object]) -> None:
        if not task.cancelled() and (exc := task.exception()) and self._first_exc is None:
            self._first_exc = exc
        self._count -= 1
        if self._count == 0:
            self._done_event.set()

    async def wait_all(self) -> None:
        """Suspend until all tracked subtasks have finished."""
        await self._done_event.wait()
        if self._first_exc is not None:
            raise self._first_exc


@dataclass
class TaskContext:
    """Context passed as the first argument to every async task function.

    Provides access to progress/cancellation state and GUI response requests.
    """

    _status: TaskStatus
    _response_requester: Callable[["ResponseRequest"], Awaitable[Response]]
    _subtask_tracker: _SubtaskTracker

    @property
    def status(self) -> TaskStatus:
        return self._status

    async def request_response(
        self,
        title: str,
        expected_responses: list[Response],
        message: str,
        dialog_type: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> Response:
        """Request a user response via the GUI; suspends until the user responds."""
        request = ResponseRequest(title, expected_responses, message, dialog_type, details)
        return await self._response_requester(request)

    async def subtask[R](self, coro: Coroutine[Any, Any, R]) -> asyncio.Task[R]:
        """Start *coro* as a concurrent asyncio task and yield control so it can begin."""
        self._subtask_tracker.task_started()
        t: asyncio.Task[R] = asyncio.create_task(coro)
        t.add_done_callback(self._subtask_tracker.task_finished)
        await asyncio.sleep(0)
        return t
