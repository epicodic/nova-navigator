import asyncio
import queue
import threading
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


@dataclass
class DecisionRequest:
    message: str
    args: dict[str, Any] | None = None


class DecisionResponse(Enum):
    YES = auto()
    YES_TO_ALL = auto()
    NO = auto()
    NO_TO_ALL = auto()

    @property
    def is_yes(self) -> bool:
        return self in {DecisionResponse.YES, DecisionResponse.YES_TO_ALL}

    @property
    def is_no(self) -> bool:
        return self in {DecisionResponse.NO, DecisionResponse.NO_TO_ALL}


Task = Generator["DecisionRequest | Task", DecisionResponse, None]


@dataclass
class Progress:
    completed: int = 0
    total: int = 0
    step_total: int = 0
    step_completed: int = 0


class TaskCancelled(Exception):
    """Raised when a task is cancelled by the user."""


class TaskStatus:
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
        if self._cancel_event.is_set():
            raise TaskCancelled

    def update_progress(self, inc_completed: int = 0, inc_total: int = 0) -> None:
        self._progress.total += inc_total
        self._progress.completed += inc_completed
        self._progress_callback(self)

    def set_progress(self, completed: int, total: int) -> None:
        self._progress.completed = completed
        self._progress.total = total
        self._progress_callback(self)

    def set_completed(self) -> None:
        self.set_progress(self._progress.total, self._progress.total)

    def update_step_progress(self, inc_completed: int = 0, inc_total: int = 0) -> None:
        self._progress.step_total += inc_total
        self._progress.step_completed += inc_completed
        self._progress_callback(self)

    def set_step_progress(self, completed: int, total: int) -> None:
        self._progress.step_completed = completed
        self._progress.step_total = total
        self._progress_callback(self)

    def set_step_completed(self) -> None:
        self.set_step_progress(self._progress.step_total, self._progress.step_total)


GuiRequestCallback = Callable[[DecisionRequest], Awaitable[DecisionResponse]]


class DecisionBroker:
    _request_queue: queue.Queue[tuple[DecisionRequest, asyncio.Future[DecisionResponse]]]
    _response_queue: queue.Queue[DecisionResponse]
    _next_decision_id: int
    _notify_task: asyncio.Task[None] | None
    _gui_request_callback: GuiRequestCallback

    def __init__(
        self,
        gui_request_callback: GuiRequestCallback,
    ) -> None:
        self._request_queue = queue.Queue()
        self._response_queue = queue.Queue()
        self._next_decision_id = 0
        self._notify_task = None
        self._gui_request_callback = gui_request_callback

    def _generate_decision_id(self) -> int:
        with self._decision_mutex:
            decision_id = self._next_decision_id
            self._next_decision_id += 1
            return decision_id

    def submit(self, request: DecisionRequest) -> None:
        future = asyncio.get_event_loop().create_future()
        self._request_queue.put((request, future))
        self._notify_gui()

    def _notify_gui(self) -> None:
        if self._notify_task is None or self._notify_task.done():
            self._notify_task = asyncio.create_task(self._notify_gui_task())

    async def _notify_gui_task(self) -> None:
        while not self._request_queue.empty():
            request, future = self._request_queue.get()
            response = await self._gui_request_callback(request)
            future.set_result(response)


class TaskScheduler:
    _waiting_tasks: list[Task]
    _decision_request_queue: queue.Queue[tuple[DecisionRequest]]

    def __init__(self):
        self._waiting_tasks = []

    def run(self, task: Task) -> None:
        while True:
            try:
                result = task.send(None)
                if isinstance(result, DecisionRequest):
                    self._waiting_tasks.append(task)
                    return
                elif isinstance(result, Task):
                    self.run(result)
                else:
                    break
            except StopIteration:
                break

    def _run_waiting_tasks(self) -> None:
        pass
