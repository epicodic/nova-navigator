import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

from nova_navigator.decision import Decision

from .context import DecisionRequest, GuiRequestCallback, TaskContext, TaskStatus


async def _create_future_in_loop() -> asyncio.Future[Decision]:
    return asyncio.get_event_loop().create_future()


async def _wait_for_future(future: asyncio.Future[Decision]) -> Decision:
    """Poll *future* (which lives in another event loop) until it is done."""
    await asyncio.sleep(0)
    while not future.done():  # noqa: ASYNC110
        await asyncio.sleep(0.01)
    return future.result()


class AsyncTaskScheduler:
    """Runs async task coroutines in a worker thread with an isolated event loop.

    Decision requests are bridged to the GUI event loop via
    :func:`asyncio.run_coroutine_threadsafe`.  An :class:`asyncio.Lock` ensures
    only one GUI dialog is in flight at a time; ``ALL``/``NONE`` responses are
    cached and applied automatically to subsequent identical requests.
    """

    _gui_request_callback: GuiRequestCallback
    _decisions_to_all: dict[str, Decision]
    _request_lock: asyncio.Lock | None

    def __init__(self, gui_request_callback: GuiRequestCallback) -> None:
        self._gui_request_callback = gui_request_callback
        self._decisions_to_all = {}
        self._request_lock = None

    @staticmethod
    async def execute(
        gui_request_callback: GuiRequestCallback,
        task_fn: Callable[[TaskContext], Awaitable[None]],
        status: TaskStatus,
    ) -> "AsyncTaskScheduler":
        """Run *task_fn* in a worker thread and await its completion."""
        gui_loop = asyncio.get_running_loop()
        scheduler = AsyncTaskScheduler(gui_request_callback)
        await asyncio.to_thread(scheduler._run_in_worker_thread, task_fn, status, gui_loop)
        return scheduler

    def _run_in_worker_thread(
        self,
        task_fn: Callable[[TaskContext], Awaitable[None]],
        status: TaskStatus,
        gui_loop: asyncio.AbstractEventLoop,
    ) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_impl(task_fn, status, gui_loop))
        finally:
            loop.close()

    async def _run_impl(
        self,
        task_fn: Callable[[TaskContext], Awaitable[None]],
        status: TaskStatus,
        gui_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._request_lock = asyncio.Lock()
        ctx = TaskContext(
            _status=status,
            _decision_requester=self._create_decision_requester(gui_loop),
        )
        await task_fn(ctx)

    def _create_decision_requester(
        self,
        gui_loop: asyncio.AbstractEventLoop,
    ) -> Callable[[str, list[Decision], str], Awaitable[Decision]]:
        async def requester(title: str, expected: list[Decision], msg: str) -> Decision:
            if title in self._decisions_to_all:
                return self._decisions_to_all[title]
            assert self._request_lock is not None
            async with self._request_lock:
                if title in self._decisions_to_all:
                    return self._decisions_to_all[title]

                request = DecisionRequest(title, expected, msg)
                gui_future: asyncio.Future[Decision] = asyncio.run_coroutine_threadsafe(
                    _create_future_in_loop(), gui_loop
                ).result()
                asyncio.run_coroutine_threadsafe(
                    cast("Coroutine[Any, Any, None]", self._gui_request_callback(request, gui_future)),
                    gui_loop,
                )
                decision = await _wait_for_future(gui_future)

                if decision.is_to_all:
                    self._decisions_to_all[title] = decision
                return decision

        return requester
