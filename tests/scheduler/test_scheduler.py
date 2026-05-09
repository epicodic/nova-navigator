import asyncio
import threading
from collections.abc import Awaitable, Callable

import pytest

from nova_navigator.response import Response
from nova_navigator.scheduler import AsyncTaskScheduler, ResponseRequest, TaskCancelled, TaskContext, TaskStatus


def _make_status() -> TaskStatus:
    return TaskStatus(cancel_event=threading.Event(), progress_callback=lambda _: None)


class TaskHarness:
    """Test helper that runs tasks via AsyncTaskScheduler and records an event log.

    Usage::

        harness = TaskHarness()
        harness.hold("Confirm", Response.YES)   # block until answer() is called
        runner = harness.start(my_task_fn)
        await harness.wait_for("some:entry")
        harness.answer("Confirm")
        await runner
        assert harness.log == [...]
    """

    log: list[str]
    gui_call_count: int
    _held: dict[str, tuple[asyncio.Event, Response]]

    def __init__(self) -> None:
        self.log = []
        self.gui_call_count = 0
        self._held = {}

    def hold(self, title: str, answer: Response) -> None:
        """Register *title* as a held response answered with *answer* when released."""
        self._held[title] = (asyncio.Event(), answer)

    def answer(self, title: str) -> None:
        """Release the held response for *title*, unblocking the waiting task."""
        event, _ = self._held[title]
        event.set()

    async def _gui_callback(self, request: ResponseRequest, future: asyncio.Future[Response]) -> None:
        self.gui_call_count += 1
        if request.title in self._held:
            event, response = self._held[request.title]
            await event.wait()
            future.set_result(response)
        else:
            future.set_result(Response.YES)

    async def run(self, task_fn: Callable[[TaskContext], Awaitable[None]], status: TaskStatus | None = None) -> None:
        """Execute *task_fn* via AsyncTaskScheduler and await its full completion."""
        await AsyncTaskScheduler.execute(self._gui_callback, task_fn, status or _make_status())

    def start(self, task_fn: Callable[[TaskContext], Awaitable[None]]) -> asyncio.Task[None]:
        """Schedule *task_fn* as an asyncio.Task so the caller can interleave awaits."""
        return asyncio.create_task(self.run(task_fn))

    async def wait_for(self, entry: str, timeout: float = 2.0) -> None:  # noqa: ASYNC109
        """Poll *log* until *entry* appears; raises AssertionError on timeout."""
        elapsed = 0.0
        interval = 0.01
        while entry not in self.log:
            if elapsed >= timeout:
                raise AssertionError(f"Timed out waiting for log entry {entry!r}. Log so far: {self.log!r}")
            await asyncio.sleep(interval)
            elapsed += interval


@pytest.mark.asyncio
async def test_scheduler_task_completes() -> None:
    """A task with no responses runs and logs its completion."""
    harness = TaskHarness()

    async def my_task(_ctx: TaskContext) -> None:
        harness.log.append("ran")

    await harness.run(my_task)
    assert harness.log == ["ran"]


@pytest.mark.asyncio
async def test_scheduler_response_routing() -> None:
    """A task that calls ctx.request_response() receives the registered answer."""
    harness = TaskHarness()
    harness.hold("Confirm", Response.YES)

    received: list[Response] = []

    async def my_task(ctx: TaskContext) -> None:
        d = await ctx.request_response("Confirm", [Response.YES, Response.NO], "Are you sure?")
        received.append(d)

    # answer immediately — no waiting needed since we answer before the response blocks
    harness.answer("Confirm")
    await harness.run(my_task)
    assert received == [Response.YES]


@pytest.mark.asyncio
async def test_blocking_subtask_with_held_response() -> None:
    """B and E both block on the same response title; C, D, F run freely in between.

    B is answered with Response.ALL (ALL), which the scheduler caches.
    E is blocked on the _request_lock while B holds it; when B releases the lock
    after receiving ALL, E finds the cached answer and unblocks immediately — so
    E finishes after B without a second GUI prompt.

    Expected log order:
        T, T:prepare A, A:runs, A:finishes,
        T:prepare B, B:runs, B:waits,
        T:prepare C, C:runs, C:finishes,
        T:prepare D, D:runs, D:finishes,
        T:prepare E, E:runs, E:waits,
        T:prepare F, F:runs, F:finishes,
        B:continues, B:finishes,
        E:continues, E:finishes,
    """
    harness = TaskHarness()
    harness.hold("Confirm", Response.ALL)

    async def task_A(_ctx: TaskContext) -> None:
        harness.log.append("A:runs")
        harness.log.append("A:finishes")

    # task_B shares the parent TaskContext; the scheduler's _request_lock ensures
    # only one response is in-flight at a time. C, D, and F don't call
    # request_response so they run freely. E also calls request_response with the
    # same title, but it blocks on the lock until B releases it. Since B's answer
    # is Response.ALL, the scheduler caches it, and E finds the cached value
    # immediately after acquiring the lock.
    async def task_B(ctx: TaskContext) -> None:
        harness.log.append("B:runs")
        harness.log.append("B:waits")
        await ctx.request_response("Confirm", [Response.YES, Response.ALL], "Proceed?")
        harness.log.append("B:continues")
        harness.log.append("B:finishes")

    async def task_C(_ctx: TaskContext) -> None:
        harness.log.append("C:runs")
        harness.log.append("C:finishes")

    async def task_D(_ctx: TaskContext) -> None:
        harness.log.append("D:runs")
        harness.log.append("D:finishes")

    async def task_E(ctx: TaskContext) -> None:
        harness.log.append("E:runs")
        harness.log.append("E:waits")
        await ctx.request_response("Confirm", [Response.YES, Response.ALL], "Proceed?")
        harness.log.append("E:continues")
        harness.log.append("E:finishes")

    async def task_F(_ctx: TaskContext) -> None:
        harness.log.append("F:runs")
        harness.log.append("F:finishes")

    async def task_T(ctx: TaskContext) -> None:
        harness.log.append("T")

        harness.log.append("T:prepare A")
        await ctx.subtask(task_A(ctx))

        harness.log.append("T:prepare B")
        await ctx.subtask(task_B(ctx))

        harness.log.append("T:prepare C")
        await ctx.subtask(task_C(ctx))

        harness.log.append("T:prepare D")
        await ctx.subtask(task_D(ctx))

        harness.log.append("T:prepare E")
        await ctx.subtask(task_E(ctx))

        harness.log.append("T:prepare F")
        await ctx.subtask(task_F(ctx))

        harness.log.append("T:finishes")

    runner = harness.start(task_T)
    await harness.wait_for("F:finishes")
    harness.answer("Confirm")
    await runner

    assert harness.log == [
        "T",
        "T:prepare A",
        "A:runs",
        "A:finishes",
        "T:prepare B",
        "B:runs",
        "B:waits",
        "T:prepare C",
        "C:runs",
        "C:finishes",
        "T:prepare D",
        "D:runs",
        "D:finishes",
        "T:prepare E",
        "E:runs",
        "E:waits",
        "T:prepare F",
        "F:runs",
        "F:finishes",
        "T:finishes",
        "B:continues",
        "B:finishes",
        "E:continues",
        "E:finishes",
    ]


@pytest.mark.asyncio
async def test_scheduler_multiple_sequential_responses() -> None:
    """Multiple sequential response requests are each routed to the GUI in order."""
    received: list[Response] = []

    async def my_task(ctx: TaskContext) -> None:
        d1 = await ctx.request_response("Q1", [Response.YES, Response.NO], "First?")
        d2 = await ctx.request_response("Q2", [Response.YES, Response.NO], "Second?")
        received.extend([d1, d2])

    harness = TaskHarness()
    harness.hold("Q1", Response.YES)
    harness.hold("Q2", Response.NO)
    harness.answer("Q1")
    harness.answer("Q2")
    await harness.run(my_task)

    assert received == [Response.YES, Response.NO]
    assert harness.gui_call_count == 2


@pytest.mark.asyncio
async def test_scheduler_all_caches_for_same_title() -> None:
    """Response.ALL for a title suppresses subsequent GUI prompts with the same title."""

    async def my_task(ctx: TaskContext) -> None:
        await ctx.subtask(ctx.request_response("Overwrite", [Response.YES, Response.ALL], "Overwrite?"))
        await ctx.subtask(ctx.request_response("Overwrite", [Response.YES, Response.ALL], "Overwrite?"))
        await ctx.subtask(ctx.request_response("Overwrite", [Response.YES, Response.ALL], "Overwrite?"))

    harness = TaskHarness()
    harness.hold("Overwrite", Response.ALL)
    harness.answer("Overwrite")
    await harness.run(my_task)

    assert harness.gui_call_count == 1


@pytest.mark.asyncio
async def test_scheduler_none_caches_for_same_title() -> None:
    """Response.NONE for a title suppresses subsequent GUI prompts with the same title."""

    async def my_task(ctx: TaskContext) -> None:
        await ctx.subtask(ctx.request_response("Delete", [Response.YES, Response.NONE], "Delete?"))
        await ctx.subtask(ctx.request_response("Delete", [Response.YES, Response.NONE], "Delete?"))

    harness = TaskHarness()
    harness.hold("Delete", Response.NONE)
    harness.answer("Delete")
    await harness.run(my_task)

    assert harness.gui_call_count == 1


@pytest.mark.asyncio
async def test_scheduler_different_titles_each_get_gui_call() -> None:
    """Requests with different titles each trigger a separate GUI callback."""

    async def my_task(ctx: TaskContext) -> None:
        await ctx.request_response("Title A", [Response.YES], "A?")
        await ctx.request_response("Title B", [Response.YES], "B?")

    harness = TaskHarness()
    # both titles are unregistered → auto-answered YES; two separate GUI calls expected
    await harness.run(my_task)

    assert harness.gui_call_count == 2


@pytest.mark.asyncio
async def test_context_subtask_runs_concurrently() -> None:
    """ctx.subtask() yields control so the child starts before the parent continues."""
    harness = TaskHarness()

    async def my_task(ctx: TaskContext) -> None:
        harness.log.append("parent:before")
        t = await ctx.subtask(_child())
        harness.log.append("parent:after")
        await t

    async def _child() -> None:
        harness.log.append("child:running")

    await harness.run(my_task)
    assert harness.log.index("child:running") < harness.log.index("parent:after")


@pytest.mark.asyncio
async def test_scheduler_exception_propagates() -> None:
    """Exceptions raised inside a task propagate out of run()."""

    async def bad_task(_ctx: TaskContext) -> None:
        raise ValueError("boom")

    harness = TaskHarness()
    with pytest.raises(ValueError, match="boom"):
        await harness.run(bad_task)


@pytest.mark.asyncio
async def test_fire_and_forget_subtask_exception_propagates() -> None:
    """An exception in a fire-and-forget subtask is re-raised after the root task returns."""

    async def root(ctx: TaskContext) -> None:
        await ctx.subtask(_bad_subtask())
        # root returns immediately; scheduler must drain and surface the subtask exception

    async def _bad_subtask() -> None:
        raise RuntimeError("subtask boom")

    harness = TaskHarness()
    with pytest.raises(RuntimeError, match="subtask boom"):
        await harness.run(root)


@pytest.mark.asyncio
async def test_cancellation_stops_task() -> None:
    """Setting the cancel event causes check_cancelled() to raise TaskCancelled."""
    cancel_event = threading.Event()
    status = TaskStatus(cancel_event=cancel_event, progress_callback=lambda _: None)
    harness = TaskHarness()
    reached_end = False

    async def my_task(ctx: TaskContext) -> None:
        nonlocal reached_end
        cancel_event.set()
        ctx.status.check_cancelled()  # should raise
        reached_end = True  # must not be reached

    with pytest.raises(TaskCancelled):
        await harness.run(my_task, status)

    assert not reached_end


@pytest.mark.asyncio
async def test_nested_subtasks_all_tracked() -> None:
    """Subtasks that themselves spawn subtasks are tracked and complete before execute() returns."""
    harness = TaskHarness()

    async def root(ctx: TaskContext) -> None:
        await ctx.subtask(_level1(ctx))
        harness.log.append("root:done")

    async def _level1(ctx: TaskContext) -> None:
        await ctx.subtask(_level2())
        harness.log.append("level1:done")

    async def _level2() -> None:
        harness.log.append("level2:done")

    await harness.run(root)

    # all three levels must have completed when run() returns
    assert "level2:done" in harness.log
    assert "level1:done" in harness.log
    assert "root:done" in harness.log
