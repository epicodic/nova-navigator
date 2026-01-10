import threading
from collections.abc import Awaitable, Callable
from enum import Enum, auto
from typing import Any

from .context import GuiRequestCallback, Progress, TaskContext, TaskStatus
from .scheduler import AsyncTaskScheduler


class Job:
    """Represents a long-running job whose progress can be tracked and that can be cancelled.

    The task function receives a :class:`TaskContext` as its first argument and is
    executed by :class:`AsyncTaskScheduler` in a worker thread.
    """

    class State(Enum):
        INITIALIZED = auto()
        RUNNING = auto()
        COMPLETED = auto()
        CANCELED = auto()

    _title: str
    _task_fn: Callable[[TaskContext], Awaitable[None]]
    _scheduler: AsyncTaskScheduler | None
    _status: TaskStatus
    _cancel_event: threading.Event
    _state: State

    def __init__(self, title: str, task_fn: Callable[..., Awaitable[None]], *args: Any, **kwargs: Any) -> None:
        self._title = title
        self._cancel_event = threading.Event()
        self._status = TaskStatus(cancel_event=self._cancel_event, progress_callback=self._progress_callback)
        self._task_fn = lambda ctx: task_fn(ctx, *args, **kwargs)
        self._scheduler = None
        self._state = self.State.INITIALIZED

    def _progress_callback(self, status: TaskStatus) -> None:
        if status.is_complete():
            self._state = self.State.COMPLETED

    async def start(self, gui_request_callback: GuiRequestCallback) -> None:
        self._state = self.State.RUNNING
        self._scheduler = await AsyncTaskScheduler.execute(gui_request_callback, self._task_fn, self._status)

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
