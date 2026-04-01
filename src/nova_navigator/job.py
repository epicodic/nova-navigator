import threading
from collections.abc import Callable
from enum import Enum, auto
from typing import Any

from .task import GuiRequestCallback, Progress, Task, TaskScheduler, TaskStatus


class Job:
    """Represents a long-running job that can be started and cancelled, and whose progress can be tracked.

    The process is driven by a Task, which performs the actual work and reports progress and cancellation status via
    a TaskStatus object. The Task and possibly spwaned subtasks are executed in a worker thread by a TaskScheduler,
    which also handles any DecisionRequests that are yielded. Those requests are forwarded to the GUI via the provided
    GuiRequestCallback, passed in to the start() method.
    """

    class State(Enum):
        INITIALIZED = auto()
        RUNNING = auto()
        COMPLETED = auto()
        # FAILED = auto()
        CANCELED = auto()

    _title: str
    _task: Task
    _scheduler: TaskScheduler | None
    _status: TaskStatus
    _cancel_event: threading.Event
    _state: State

    def __init__(self, title: str, task_fn: Callable[..., Task], *args: Any, **kwargs: Any) -> None:
        self._title = title
        self._cancel_event = threading.Event()
        self._status = TaskStatus(cancel_event=self._cancel_event, progress_callback=self._progress_callback)
        self._task = task_fn(self._status, *args, **kwargs)
        self._scheduler = None
        self._state = self.State.INITIALIZED

    def _progress_callback(self, status: TaskStatus) -> None:
        if status.is_complete():
            self._state = self.State.COMPLETED

    async def start(self, gui_request_callback: GuiRequestCallback) -> None:
        self._state = self.State.RUNNING
        self._scheduler = await TaskScheduler.execute(gui_request_callback, [self._task])

    def cancel(self) -> None:
        self._cancel_event.set()

    @property
    def title(self) -> str:
        return self._title

    @property
    def state(self) -> State:
        return self._state

    @property
    def progress(self) -> Progress:
        return self._status.progress
