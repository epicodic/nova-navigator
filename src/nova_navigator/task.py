import asyncio
import concurrent.futures
import inspect
import threading
import time
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from functools import wraps
from typing import Any

from nova_navigator.decision import Decision

from .base.thread_safe_list import ThreadSafeList


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


Task = Generator["DecisionRequest | Task", Decision, None]


def task(func: Callable[..., Any]) -> Callable[..., Task]:
    """Decorator that normalises a plain function into a :data:`Task`.

    If *func* is already a generator function it is returned unchanged.
    Otherwise a thin wrapper is added so callers can treat it uniformly as a
    generator regardless of whether it ever yields.
    """
    if inspect.isgeneratorfunction(func):
        return func

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Task:
        result = func(*args, **kwargs)
        return result
        yield  # type: ignore[unreachable]

    return wrapper


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


class TaskScheduler:
    r"""Runs :data:`Task`\\ s in a thread and routes :class:`DecisionRequest`\\ s to the GUI.

    Tasks yield either a :class:`DecisionRequest` (pause for user input) or a
    nested :data:`Task` (sub-task to run inline).  When a request is yielded
    the scheduler suspends the task, notifies the GUI via *gui_request_callback*,
    and resumes the task with the :class:`Decision` once the user
    replies.  ``YES_TO_ALL`` / ``NO_TO_ALL`` responses are cached and applied
    automatically to subsequent identical requests.
    """

    _waiting_tasks_with_requests: ThreadSafeList[tuple[Task, DecisionRequest]]
    _ready_tasks_with_responses: ThreadSafeList[tuple[Task, Decision]]
    _notify_task: concurrent.futures.Future[None] | None
    _gui_request_callback: GuiRequestCallback
    _event_loop: asyncio.AbstractEventLoop

    _decisions_to_all: dict[str, Decision]

    def __init__(self, gui_request_callback: GuiRequestCallback, event_loop: asyncio.AbstractEventLoop) -> None:
        self._waiting_tasks_with_requests = ThreadSafeList()
        self._ready_tasks_with_responses = ThreadSafeList()
        self._notify_task = None
        self._gui_request_callback = gui_request_callback
        self._event_loop = event_loop
        self._decisions_to_all = {}

    @staticmethod
    async def execute(gui_request_callback: GuiRequestCallback, tasks: list[Task]) -> "TaskScheduler":
        """Create a scheduler and run *tasks* in a worker thread, awaiting completion."""
        loop = asyncio.get_running_loop()
        scheduler = TaskScheduler(gui_request_callback=gui_request_callback, event_loop=loop)
        await asyncio.create_task(asyncio.to_thread(scheduler.run_tasks, tasks))
        return scheduler

    def run_tasks(self, tasks: list[Task]) -> None:
        """Run a list of tasks sequentially, blocking until all have finished."""

        def task_spawner() -> Task:
            for task in tasks:  # noqa: UP028
                yield task

        self.run_task(task_spawner())

    def run_task(self, task: Task) -> None:
        """Run a single task to completion, processing any pending ready-tasks along the way."""
        self._run_task(task)

        # keep running until all pending tasks are done
        while len(self._waiting_tasks_with_requests) > 0 or len(self._ready_tasks_with_responses) > 0:
            self._run_ready_tasks()
            time.sleep(0.1)  # avoid busy waiting

    def _run_task(self, task: Task, decision: Decision | None = None) -> None:
        # decision is only allowed to be not None if the task is waiting for a response
        # otherwise, we we get an exception during the first send()
        while True:
            self._run_ready_tasks()  # process any tasks that became ready while we were running this one
            try:
                result = task.send(decision)  # ty:ignore[invalid-argument-type] # None is valid for first send()
                if isinstance(result, DecisionRequest):
                    decision = self.submit_request(task, result)
                    if decision is not None:
                        continue  # continue immediately if we got a response

                    return  # task is suspended waiting for user input
                elif isinstance(result, Generator):
                    self._run_task(result)
                else:
                    break  # type: ignore[unreachable]
            except StopIteration:
                break

    def _run_ready_tasks(self) -> None:
        while len(self._ready_tasks_with_responses) > 0:
            task, response = self._ready_tasks_with_responses.pop_front()
            self._run_task(task, response)

    def submit_request(self, task: Task, request: DecisionRequest) -> Decision | None:
        """Submit a decision request, returning a cached response immediately or ``None`` if queued."""
        # check if there is a global answer for this request already -> then respond immediately
        if request.title in self._decisions_to_all:
            return self._decisions_to_all[request.title]

        self._waiting_tasks_with_requests.append((task, request))
        self._notify_gui()
        return None

    def _notify_gui(self) -> None:
        if self._notify_task is None or self._notify_task.done():
            self._notify_task = asyncio.run_coroutine_threadsafe(self._notify_gui_task(), self._event_loop)
            # loop.create_task(self._notify_gui_task())

    async def _notify_gui_task(self) -> None:
        while len(self._waiting_tasks_with_requests) > 0:
            task, request = self._waiting_tasks_with_requests.peek_front()
            future: asyncio.Future[Decision] = asyncio.get_event_loop().create_future()
            await self._gui_request_callback(request, future)
            await future
            response = future.result()
            self._ready_tasks_with_responses.append((task, response))
            self._waiting_tasks_with_requests.pop_front()

            if response.is_to_all:
                self._answer_all(request, response)

    def _answer_all(self, answered_request: DecisionRequest, response: Decision) -> None:
        if not response.is_to_all:
            return

        self._decisions_to_all[answered_request.title] = response

        with self._waiting_tasks_with_requests as waiting_tasks:
            remaining_waiting_tasks: list[tuple[Task, DecisionRequest]] = []
            for task, req in waiting_tasks:
                if req.title == answered_request.title:
                    self._ready_tasks_with_responses.append((task, response))
                else:
                    remaining_waiting_tasks.append((task, req))
            waiting_tasks[:] = remaining_waiting_tasks
