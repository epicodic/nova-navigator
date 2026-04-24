import asyncio
import threading
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from nova_navigator.decision import Decision


@dataclass(init=False)
class DecisionRequest:
    """A request for a user decision, yielded by a task.

    The *message* is a format string that may reference *kwargs* for display.
    The *title* also serves as the deduplication key inside :class:`TaskScheduler` so
    that ``ALL`` / ``NO`` responses suppress identical prompts.
    """

    title: str
    expected_decisions: list[Decision]
    message: str

    def __init__(self, title: str, expected_decisions: list[Decision], message: str) -> None:
        self.title = title
        self.expected_decisions = expected_decisions
        self.message = message


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


GuiRequestCallback = Callable[[DecisionRequest, asyncio.Future[Decision]], Awaitable[None]]


@dataclass
class TaskContext:
    """Context passed as the first argument to every async task function.

    Provides access to progress/cancellation state and GUI decision requests.
    """

    _status: TaskStatus
    _decision_requester: Callable[[str, list[Decision], str], Awaitable[Decision]]

    @property
    def status(self) -> TaskStatus:
        return self._status

    async def request_decision(
        self,
        title: str,
        expected_decisions: list[Decision],
        message: str,
    ) -> Decision:
        """Request a user decision via the GUI; suspends until the user responds."""
        return await self._decision_requester(title, expected_decisions, message)

    async def subtask[R](self, coro: Coroutine[Any, Any, R]) -> asyncio.Task[R]:
        """Start *coro* as a concurrent asyncio task and yield control so it can begin."""
        t: asyncio.Task[R] = asyncio.create_task(coro)
        await asyncio.sleep(0)
        return t
