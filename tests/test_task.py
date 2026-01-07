import asyncio
import time

import pytest

from nova_navigator.task import Decision, DecisionRequest, Task, TaskScheduler, task

actual_task_order: list[int] = []


@task
def task1(id: int, input: str, expected_decisions: list[Decision] | None = None) -> Task:
    if input[0:3] == "ask":
        response = yield DecisionRequest(
            input,
            expected_decisions or [Decision.YES, Decision.NO],
            input,
        )
        if response.is_negative:
            return

    print(f"Task 1 ({id}): running...")
    actual_task_order.append(id)
    time.sleep(0.01)
    print(f"Task 1 ({id}): completed.")


@task
def task2(id: int) -> None:
    print(f"Task 2 ({id}): running...")
    actual_task_order.append(id)
    time.sleep(0.01)
    print(f"Task 2 ({id}): completed.")


@pytest.mark.asyncio
async def test_task_scheduler_basic() -> None:
    global actual_task_order  # noqa: PLW0603
    actual_task_order = []
    tasks = [
        task1(1, "ask1"),
        task1(2, "no_ask"),
        task1(3, "ask1", [Decision.YES, Decision.NO, Decision.ALL, Decision.NONE]),
        task1(4, "no_ask"),
        task1(5, "ask2"),
        task1(6, "no_ask"),
        task2(id=7),
        task1(8, "ask1"),
        task1(9, "no_ask"),
        task1(10, "ask3"),
    ]
    expected_task_order = [2, 4, 6, 7, 9, 1, 8, 3, 10]

    async def gui_callback(
        request: DecisionRequest,
        future: asyncio.Future[Decision],
    ) -> None:
        print(f"GUI received request: '{request.message}'")
        # Simulate user interaction delay
        if request.message == "ask1":
            await asyncio.sleep(0.1)
            result = Decision.ALL if Decision.ALL in request.expected_decisions else Decision.YES
            print(f"GUI responding {result} to '{request.message}'")
            future.set_result(result)
        if request.message == "ask2":
            await asyncio.sleep(0.1)
            print(f"GUI responding NO to '{request.message}'")
            future.set_result(Decision.NO)
        if request.message == "ask3":
            await asyncio.sleep(0.1)
            print(f"GUI responding YES to '{request.message}'")
            future.set_result(Decision.YES)

    await TaskScheduler.execute(gui_request_callback=gui_callback, tasks=tasks)

    assert actual_task_order == expected_task_order


@task
def task3(id: int, input: str) -> Task:
    print("Task 3: running...")
    actual_task_order.append(id)
    time.sleep(0.01)
    for i in range(3):
        yield task1(id=id + 1 + i, input=f"{input}_{id}_{i}")
    print("Task 3: completed.")


@pytest.mark.asyncio
async def test_task_scheduler_task_spawn() -> None:
    tasks = [
        task3(0, "ask3"),
        task3(4, "no_ask3"),
    ]

    expected_task_order = [0, 4, 5, 6, 7, 1, 2, 3]

    global actual_task_order  # noqa: PLW0603
    actual_task_order = []

    async def gui_callback(
        request: DecisionRequest,
        future: asyncio.Future[Decision],
    ) -> None:
        print(f"GUI received request: '{request.message}'")
        await asyncio.sleep(0.1)
        future.set_result(Decision.YES)

    await TaskScheduler.execute(gui_request_callback=gui_callback, tasks=tasks)
    assert actual_task_order == expected_task_order
