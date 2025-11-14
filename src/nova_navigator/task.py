import asyncio
import concurrent.futures
import inspect
import threading
import time
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from enum import Enum, auto
from functools import wraps
from typing import Any

from .base.thread_safe_list import ThreadSafeList


@dataclass(init=False)
class DecisionRequest:
    message: str
    kwargs: dict[str, Any]

    def __init__(self, message: str, **kwargs: Any) -> None:
        self.message = message
        self.kwargs = kwargs


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

    @property
    def is_to_all(self) -> bool:
        return self in {DecisionResponse.YES_TO_ALL, DecisionResponse.NO_TO_ALL}


Task = Generator["DecisionRequest | Task", DecisionResponse, None]


def task(func : Callable[..., Any]) -> Callable[..., Task]:
    if inspect.isgeneratorfunction(func):
        return func

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Task:
        result = func(*args, **kwargs)
        return result
        yield # yield to make this a generator
    return wrapper


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


GuiRequestCallback = Callable[[DecisionRequest, asyncio.Future[DecisionResponse]], Awaitable[None]]


class TaskScheduler:
    _waiting_tasks_with_requests: ThreadSafeList[tuple[Task, DecisionRequest]]
    _ready_tasks_with_responses: ThreadSafeList[tuple[Task, DecisionResponse]]
    _notify_task: concurrent.futures.Future[None] | None
    _gui_request_callback: GuiRequestCallback
    _event_loop: asyncio.AbstractEventLoop

    _decisions_for_all: dict[str, DecisionResponse]

    def __init__(self, gui_request_callback: GuiRequestCallback, event_loop: asyncio.AbstractEventLoop) -> None:
        self._waiting_tasks_with_requests = ThreadSafeList()
        self._ready_tasks_with_responses = ThreadSafeList()
        self._notify_task = None
        self._gui_request_callback = gui_request_callback
        self._event_loop = event_loop
        self._decisions_for_all = {}

    @staticmethod
    async def execute(gui_request_callback: GuiRequestCallback, tasks: list[Task]) -> None:
        loop = asyncio.get_running_loop()
        scheduler = TaskScheduler(gui_request_callback=gui_request_callback, event_loop=loop)
        await asyncio.create_task(asyncio.to_thread(scheduler.run_tasks, tasks))

    def run_tasks(self, tasks: list[Task]) -> None:
        def task_spawner() -> Task:
            for task in tasks:  # noqa: UP028
                yield task

        self.run_task(task_spawner())

    def run_task(self, task: Task) -> None:
        self._run_task(task)

        # keep running until all pending tasks are done
        while len(self._waiting_tasks_with_requests) > 0 or len(self._ready_tasks_with_responses) > 0:
            self._run_ready_tasks()
            time.sleep(0.1)  # avoid busy waiting

        print("All tasks completed.")

    def _run_task(self, task: Task, decision: DecisionResponse | None = None) -> None:
        # decision is only allowed to be not None if the task is waiting for a response
        # otherwise, we we get an exception during the first send()
        while True:
            self._run_ready_tasks()  # process any tasks that became ready while we were running this one
            try:
                result = task.send(decision)  # type: ignore - type checker doesn't like None on first send()
                if isinstance(result, DecisionRequest):
                    decision = self.submit_request(task, result)
                    if decision is not None:
                        continue  # continue immediately if we got a response

                    return  # task is suspended waiting for user input
                elif isinstance(result, Generator):
                    self._run_task(result)
                else:
                    break
            except StopIteration:
                break

    def _run_ready_tasks(self) -> None:
        while len(self._ready_tasks_with_responses) > 0:
            task, response = self._ready_tasks_with_responses.pop_front()
            self._run_task(task, response)

    def submit_request(self, task: Task, request: DecisionRequest) -> DecisionResponse | None:
        # check if there is a global answer for this request already -> then respond immediately
        if request.message in self._decisions_for_all:
            return self._decisions_for_all[request.message]

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
            print(f"Notifying GUI for request: {request.message} with args {request.kwargs}")
            future: asyncio.Future[DecisionResponse] = asyncio.get_event_loop().create_future()
            await self._gui_request_callback(request, future)
            await future
            response = future.result()
            self._ready_tasks_with_responses.append((task, response))
            self._waiting_tasks_with_requests.pop_front()

            if response.is_to_all:
                self._answer_all(request, response)

    def _answer_all(self, answered_request: DecisionRequest, response: DecisionResponse) -> None:
        if not response.is_to_all:
            return

        self._decisions_for_all[answered_request.message] = response

        with self._waiting_tasks_with_requests as waiting_tasks:
            remaining_waiting_tasks: list[tuple[Task, DecisionRequest]] = []
            for task, req in waiting_tasks:
                if req.message == answered_request.message:
                    self._ready_tasks_with_responses.append((task, response))
                else:
                    remaining_waiting_tasks.append((task, req))
            waiting_tasks[:] = remaining_waiting_tasks
