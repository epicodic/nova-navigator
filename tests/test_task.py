import asyncio
import threading
import time
from collections.abc import Awaitable, Callable

import pytest

from nova_navigator.task import (
    AsyncTaskScheduler,
    Decision,
    DecisionRequest,
    TaskContext,
    TaskStatus,
)


def _make_status() -> TaskStatus:
    return TaskStatus(cancel_event=threading.Event(), progress_callback=lambda _: None)


async def _run(
    task_fn: Callable[[TaskContext], Awaitable[None]],
    decisions: list[Decision] | None = None,
    status: TaskStatus | None = None,
) -> list[DecisionRequest]:
    """Run task_fn via AsyncTaskScheduler with simulated GUI decisions."""
    pending = list(decisions or [])
    requests: list[DecisionRequest] = []
    if status is None:
        status = _make_status()

    async def gui_callback(request: DecisionRequest, future: asyncio.Future[Decision]) -> None:
        requests.append(request)
        if not pending:
            raise AssertionError(f"Unexpected decision request: {request.title!r}")
        future.set_result(pending.pop(0))

    await AsyncTaskScheduler.execute(gui_callback, task_fn, status)
    return requests


@pytest.mark.asyncio
async def test_scheduler_simple_task_completes() -> None:
    """A task with no decisions runs and completes."""
    ran = []

    async def my_task(_ctx: TaskContext) -> None:
        ran.append(True)
        time.sleep(0.01)  # noqa: ASYNC251 — blocking I/O is intentional in worker thread

    await _run(my_task)
    assert ran == [True]


@pytest.mark.asyncio
async def test_scheduler_routes_decision_to_gui() -> None:
    """A task that requests a decision receives the GUI response."""
    received: list[Decision] = []

    async def my_task(ctx: TaskContext) -> None:
        d = await ctx.request_decision("Confirm", [Decision.YES, Decision.NO], "Are you sure?")
        received.append(d)

    requests = await _run(my_task, decisions=[Decision.YES])
    assert len(requests) == 1
    assert requests[0].title == "Confirm"
    assert received == [Decision.YES]


@pytest.mark.asyncio
async def test_scheduler_multiple_sequential_decisions() -> None:
    """Multiple sequential decision requests are each routed to the GUI."""
    received: list[Decision] = []

    async def my_task(ctx: TaskContext) -> None:
        d1 = await ctx.request_decision("Q1", [Decision.YES, Decision.NO], "First?")
        d2 = await ctx.request_decision("Q2", [Decision.YES, Decision.NO], "Second?")
        received.extend([d1, d2])

    requests = await _run(my_task, decisions=[Decision.YES, Decision.NO])
    assert len(requests) == 2
    assert received == [Decision.YES, Decision.NO]


@pytest.mark.asyncio
async def test_scheduler_all_caches_for_same_title() -> None:
    """ALL response for a title suppresses subsequent GUI prompts with the same title."""

    async def my_task(ctx: TaskContext) -> None:
        t1 = await ctx.subtask(_ask(ctx, "Overwrite"))
        t2 = await ctx.subtask(_ask(ctx, "Overwrite"))
        t3 = await ctx.subtask(_ask(ctx, "Overwrite"))
        await asyncio.gather(t1, t2, t3)

    async def _ask(ctx: TaskContext, title: str) -> None:
        await ctx.request_decision(title, [Decision.YES, Decision.ALL], "Overwrite?")

    nonlocal_count = [0]

    async def gui_cb(_request: DecisionRequest, future: asyncio.Future[Decision]) -> None:
        nonlocal_count[0] += 1
        future.set_result(Decision.ALL)

    await AsyncTaskScheduler.execute(gui_cb, my_task, _make_status())
    assert nonlocal_count[0] == 1


@pytest.mark.asyncio
async def test_scheduler_none_caches_for_same_title() -> None:
    """NONE response suppresses subsequent prompts with the same title."""
    gui_call_count = [0]

    async def my_task(ctx: TaskContext) -> None:
        t1 = await ctx.subtask(_ask(ctx))
        t2 = await ctx.subtask(_ask(ctx))
        await asyncio.gather(t1, t2)

    async def _ask(ctx: TaskContext) -> None:
        await ctx.request_decision("Delete", [Decision.YES, Decision.NONE], "Delete?")

    async def gui_cb(_request: DecisionRequest, future: asyncio.Future[Decision]) -> None:
        gui_call_count[0] += 1
        future.set_result(Decision.NONE)

    await AsyncTaskScheduler.execute(gui_cb, my_task, _make_status())
    assert gui_call_count[0] == 1


@pytest.mark.asyncio
async def test_scheduler_different_titles_each_get_gui_call() -> None:
    """Requests with different titles each trigger a separate GUI callback."""
    gui_call_count = [0]

    async def my_task(ctx: TaskContext) -> None:
        await ctx.request_decision("Title A", [Decision.YES], "A?")
        await ctx.request_decision("Title B", [Decision.YES], "B?")

    async def gui_cb(_request: DecisionRequest, future: asyncio.Future[Decision]) -> None:
        gui_call_count[0] += 1
        future.set_result(Decision.YES)

    await AsyncTaskScheduler.execute(gui_cb, my_task, _make_status())
    assert gui_call_count[0] == 2


@pytest.mark.asyncio
async def test_context_subtask_runs_concurrently() -> None:
    """ctx.subtask() starts the sub-task immediately (before parent continues)."""
    log: list[str] = []

    async def my_task(ctx: TaskContext) -> None:
        log.append("parent: before subtask")
        t = await ctx.subtask(_child(log))
        log.append("parent: after subtask launch")
        await t

    async def _child(log: list[str]) -> None:
        log.append("child: running")

    await _run(my_task)
    assert log.index("child: running") < log.index("parent: after subtask launch")


@pytest.mark.asyncio
async def test_scheduler_exception_propagates() -> None:
    """Exceptions raised inside a task propagate out of execute()."""

    async def bad_task(_ctx: TaskContext) -> None:
        raise ValueError("boom")

    async def noop_cb(req: DecisionRequest, fut: asyncio.Future[Decision]) -> None:
        pass

    with pytest.raises(ValueError, match="boom"):
        await AsyncTaskScheduler.execute(noop_cb, bad_task, _make_status())
