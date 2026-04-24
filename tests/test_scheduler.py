import asyncio
import threading
from collections.abc import Awaitable, Callable

import pytest

from nova_navigator.decision import Decision
from nova_navigator.scheduler import AsyncTaskScheduler, DecisionRequest, TaskContext, TaskStatus


def _make_status() -> TaskStatus:
    return TaskStatus(cancel_event=threading.Event(), progress_callback=lambda _: None)


class TaskHarness:
    """Test helper that runs tasks via AsyncTaskScheduler and records an event log.

    Usage::

        harness = TaskHarness()
        harness.hold("Confirm", Decision.YES)   # block until answer() is called
        runner = harness.start(my_task_fn)
        await harness.wait_for("some:entry")
        harness.answer("Confirm")
        await runner
        assert harness.log == [...]
    """

    log: list[str]
    _held: dict[str, tuple[asyncio.Event, Decision]]

    def __init__(self) -> None:
        self.log = []
        self._held = {}

    def hold(self, title: str, answer: Decision) -> None:
        """Register *title* as a held decision answered with *answer* when released."""
        self._held[title] = (asyncio.Event(), answer)

    def answer(self, title: str) -> None:
        """Release the held decision for *title*, unblocking the waiting task."""
        event, _ = self._held[title]
        event.set()

    async def _gui_callback(self, request: DecisionRequest, future: asyncio.Future[Decision]) -> None:
        if request.title in self._held:
            event, decision = self._held[request.title]
            await event.wait()
            future.set_result(decision)
        else:
            future.set_result(Decision.YES)

    async def run(self, task_fn: Callable[[TaskContext], Awaitable[None]]) -> None:
        """Execute *task_fn* via AsyncTaskScheduler and await its full completion."""
        await AsyncTaskScheduler.execute(self._gui_callback, task_fn, _make_status())

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
    """A task with no decisions runs and logs its completion."""
    harness = TaskHarness()

    async def my_task(_ctx: TaskContext) -> None:
        harness.log.append("ran")

    await harness.run(my_task)
    assert harness.log == ["ran"]


@pytest.mark.asyncio
async def test_scheduler_decision_routing() -> None:
    """A task that calls ctx.request_decision() receives the registered answer."""
    harness = TaskHarness()
    harness.hold("Confirm", Decision.YES)

    received: list[Decision] = []

    async def my_task(ctx: TaskContext) -> None:
        d = await ctx.request_decision("Confirm", [Decision.YES, Decision.NO], "Are you sure?")
        received.append(d)

    # answer immediately — no waiting needed since we answer before the decision blocks
    harness.answer("Confirm")
    await harness.run(my_task)
    assert received == [Decision.YES]


@pytest.mark.asyncio
async def test_blocking_subtask_with_held_decision() -> None:
    """B and E both block on the same decision title; C, D, F run freely in between.

    B is answered with Decision.ALL (YES_TO_ALL), which the scheduler caches.
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
        T:finishes
    """
    harness = TaskHarness()
    harness.hold("Confirm", Decision.ALL)

    async def task_A(_ctx: TaskContext) -> None:
        harness.log.append("A:runs")
        harness.log.append("A:finishes")

    # task_B shares the parent TaskContext; the scheduler's _request_lock ensures
    # only one decision is in-flight at a time. C, D, and F don't call
    # request_decision so they run freely. E also calls request_decision with the
    # same title, but it blocks on the lock until B releases it. Since B's answer
    # is Decision.ALL, the scheduler caches it, and E finds the cached value
    # immediately after acquiring the lock.
    async def task_B(ctx: TaskContext) -> None:
        harness.log.append("B:runs")
        harness.log.append("B:waits")
        await ctx.request_decision("Confirm", [Decision.YES, Decision.ALL], "Proceed?")
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
        await ctx.request_decision("Confirm", [Decision.YES, Decision.ALL], "Proceed?")
        harness.log.append("E:continues")
        harness.log.append("E:finishes")

    async def task_F(_ctx: TaskContext) -> None:
        harness.log.append("F:runs")
        harness.log.append("F:finishes")

    async def task_T(ctx: TaskContext) -> None:
        harness.log.append("T")

        harness.log.append("T:prepare A")
        t_a = await ctx.subtask(task_A(ctx))

        harness.log.append("T:prepare B")
        t_b = await ctx.subtask(task_B(ctx))

        harness.log.append("T:prepare C")
        t_c = await ctx.subtask(task_C(ctx))

        harness.log.append("T:prepare D")
        t_d = await ctx.subtask(task_D(ctx))

        harness.log.append("T:prepare E")
        t_e = await ctx.subtask(task_E(ctx))

        harness.log.append("T:prepare F")
        t_f = await ctx.subtask(task_F(ctx))

        await asyncio.gather(t_a, t_b, t_c, t_d, t_e, t_f)
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
        "B:continues",
        "B:finishes",
        "E:continues",
        "E:finishes",
        "T:finishes",
    ]
