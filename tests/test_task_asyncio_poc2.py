"""Proof of concept: comparing generator-based tasks vs asyncio-based tasks.

This test demonstrates that asyncio with TaskGroup + sleep(0) can replicate
the current generator-based execution model where:
1. Subtasks are launched sequentially
2. When a subtask blocks on user input, the parent continues launching more subtasks
3. All subtasks must complete before the parent completes
"""

import asyncio
import time
from asyncio import Task
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import pytest

from nova_navigator.decision import Decision
from nova_navigator.task import DecisionRequest

# ==============================================================================
# Shared infrastructure for both implementations
# ==============================================================================

execution_log: list[str] = []


def log(msg: str) -> None:
    """Thread-safe logging for execution order tracking."""
    execution_log.append(msg)
    print(msg)


@dataclass
class TaskContext:
    """Context passed to all async tasks, providing access to progress tracking and task spawning."""

    _decision_requester: Callable[[str, list[Decision], str], Awaitable[Decision]]

    async def request_decision(self, title: str, expected_decisions: list[Decision], message: str) -> Decision:
        """Request a decision from the user."""
        return await self._decision_requester(title, expected_decisions, message)

    # async def spawn(self, coro: Awaitable[None]) -> None:
    #     """Spawn a subtask that will be awaited before the parent completes.

    #     The spawned task runs concurrently but the parent will not complete until all
    #     spawned tasks complete. This is managed by the TaskGroup context.
    #     """
    #     # This method will be overridden by the parent to track spawned tasks
    #     await coro

    async def subtask[RetVal](self, coro: Coroutine[Any, Any, RetVal]) -> Task[RetVal]:
        task = asyncio.create_task(coro)
        await asyncio.sleep(0)  # Yield control so subtask can start
        return task


async def async_task_without_decision(_ctx: TaskContext, task_id: int, delay: float) -> None:
    """An async task that needs user input."""
    log(f"async_task_{task_id}: starting")
    log(f"async_task_{task_id}: working")
    time.sleep(delay)  # noqa: ASYNC251 - we want to block here to simulate a long-running operation after the decision
    log(f"async_task_{task_id}: completed")


async def async_task_with_decision(ctx: TaskContext, task_id: int, decision_title: str, delay: float) -> None:
    """An async task that needs user input."""
    log(f"async_task_{task_id}: starting")
    log(f"async_task_{task_id}: requesting decision '{decision_title}'")
    decision = await ctx.request_decision(
        decision_title,
        [Decision.YES, Decision.NO],
        f"Task {task_id} needs decision",
    )
    log(f"async_task_{task_id}: got decision {decision}")
    log(f"async_task_{task_id}: working")
    time.sleep(delay)  # noqa: ASYNC251 - we want to block here to simulate a long-running operation after the decision
    log(f"async_task_{task_id}: completed")


async def async_parent_with_subtasks(ctx: TaskContext, parent_id: int) -> None:
    """An async parent task that spawns multiple subtasks."""
    log(f"async_parent_{parent_id}: starting")

    # Create a list to track spawned tasks
    spawned_tasks = []

    log(f"async_parent_{parent_id}: creating subtask 1")
    # Spawn subtask with same context
    task = await ctx.subtask(async_task_with_decision(ctx, parent_id * 100 + 1, f"decision_{parent_id}_1", 0.01))
    spawned_tasks.append(task)

    log(f"async_parent_{parent_id}: creating subtask 2")
    task = await ctx.subtask(async_task_without_decision(ctx, parent_id * 100 + 2, 0.02))
    spawned_tasks.append(task)

    log(f"async_parent_{parent_id}: working")
    time.sleep(0.01)  # noqa: ASYNC251 - we want to block here to simulate a long-running operation after the decision
    log(f"async_parent_{parent_id}: completed")

    # Wait for all spawned tasks
    # if spawned_tasks:
    #     await asyncio.gather(*spawned_tasks)

    log(f"async_parent_{parent_id}: all subtasks completed")


# ==============================================================================
# Asyncio TaskScheduler (simplified proof of concept)
# ==============================================================================


class AsyncTaskScheduler:
    """Simplified asyncio-based task scheduler for proof of concept.

    NOTE: This POC version runs in the same event loop as the test (no worker thread).
    The real implementation will use a worker thread with its own loop.
    """

    def __init__(
        self,
        gui_request_callback: Callable[[DecisionRequest, asyncio.Future[Decision]], Awaitable[None]],
    ):
        self._gui_request_callback = gui_request_callback
        self._decisions_to_all: dict[str, Decision] = {}

    @staticmethod
    async def execute(
        gui_request_callback: Callable[[DecisionRequest, asyncio.Future[Decision]], Awaitable[None]],
        tasks: list[Callable[[TaskContext], Awaitable[None]]],
    ) -> "AsyncTaskScheduler":
        """Execute tasks - POC version runs in current event loop."""
        scheduler = AsyncTaskScheduler(gui_request_callback)
        await scheduler.run_tasks(tasks)
        return scheduler

    async def run_tasks(self, tasks: list[Callable[[TaskContext], Awaitable[None]]]) -> None:
        """Run tasks sequentially with decision request support."""
        # Create the task context
        ctx = TaskContext(_decision_requester=self._create_decision_requester())

        for task_fn in tasks:
            await task_fn(ctx)

    def _create_decision_requester(self) -> Callable[[str, list[Decision], str], Awaitable[Decision]]:
        """Create a decision requester function."""

        async def requester(title: str, expected: list[Decision], msg: str) -> Decision:
            # Check for cached ALL/NONE responses
            if title in self._decisions_to_all:
                log(f"  [scheduler] returning cached decision for '{title}'")
                return self._decisions_to_all[title]

            log(f"  [scheduler] requesting '{title}' from GUI")
            request = DecisionRequest(title, expected, msg)

            # Create future and call GUI callback
            future: asyncio.Future[Decision] = asyncio.Future()
            log(f"  [scheduler] calling GUI callback for '{title}'")
            await self._gui_request_callback(request, future)
            log(f"  [scheduler] GUI callback returned for '{title}', awaiting future")
            decision = await future
            log(f"  [scheduler] got decision {decision} for '{title}'")

            if decision.is_to_all:
                self._decisions_to_all[title] = decision

            return decision

        return requester


@pytest.mark.asyncio
async def test_asyncio_subtask_blocking_allows_parent_to_continue() -> None:
    """Test that asyncio tasks with sleep(0) allow parent to continue when subtask blocks."""
    global execution_log
    execution_log = []

    decisions_made = []

    async def gui_callback(request: DecisionRequest, future: asyncio.Future[Decision]) -> None:
        """Simulated GUI dialog - responds after a delay."""
        log(f"  [GUI] received request: '{request.title}'")
        decisions_made.append(request.title)
        await asyncio.sleep(0.05)  # Simulate user thinking
        log(f"  [GUI] responding YES to '{request.title}'")
        future.set_result(Decision.YES)

    # Create tasks: parent spawns 3 subtasks that each need decisions
    tasks = [lambda ctx: async_parent_with_subtasks(ctx, parent_id=1)]

    await AsyncTaskScheduler.execute(gui_request_callback=gui_callback, tasks=tasks)

    # Verify execution order
    print("\n=== Asyncio Execution Log ===")
    for entry in execution_log:
        print(entry)

    # Key assertions:
    # 1. All 3 subtasks should be launched before the parent finishes
    assert "async_parent_1: creating subtask 0" in execution_log
    assert "async_parent_1: creating subtask 1" in execution_log
    assert "async_parent_1: creating subtask 2" in execution_log

    # 2. Parent continues launching while subtasks are blocked
    launch_0_idx = execution_log.index("async_parent_1: creating subtask 0")
    launch_1_idx = execution_log.index("async_parent_1: creating subtask 1")
    launch_2_idx = execution_log.index("async_parent_1: creating subtask 2")
    parent_complete_idx = execution_log.index("async_parent_1: all subtasks completed")

    assert launch_0_idx < launch_1_idx < launch_2_idx < parent_complete_idx

    # 3. All decision requests should be made
    assert len(decisions_made) == 3

    # 4. Verify that "subtask X launched, continuing" appears after creating each subtask
    # This proves that sleep(0) yields control
    for i in range(3):
        create_idx = execution_log.index(f"async_parent_1: creating subtask {i}")
        continue_idx = execution_log.index(f"async_parent_1: subtask {i} launched, continuing")
        assert create_idx < continue_idx
