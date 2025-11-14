import asyncio
import time

import pytest

from nova_navigator.task import DecisionRequest, DecisionResponse, Task, TaskScheduler, task

actual_task_order : list[int] = []

#@task
def task1(id: int, input: str, all: bool = False) -> Task:
    if input[0:3] == "ask":
        response = yield DecisionRequest(input, id=id, input=input, all=all)
        if response.is_no:
            return

    print(f"Task 1 ({id}): running...")
    time.sleep(0.01)
    print(f"Task 1 ({id}): completed.")
    actual_task_order.append(id)


@task
def task2(id: int) -> None:
    print(f"Task 2 ({id}): running...")
    time.sleep(0.01)
    print(f"Task 2 ({id}): completed.")
    actual_task_order.append(id)

@pytest.mark.asyncio
async def test_task_scheduler_basic() -> None:

    global actual_task_order  # noqa: PLW0603
    actual_task_order = []
    tasks = [
        task1(1, "ask1"),
        task1(2, "no_ask"),
        task1(3, "ask1", all=True),
        task1(4, "no_ask"),
        task1(5, "ask2"),
        task1(6, "no_ask"),
        task2(id=7),
        task1(8, "ask1"),
        task1(9, "no_ask"),
        task1(10, "ask3"),
        ]
    expected_task_order = [2,4,6,8,1,7,3,9]

    async def gui_callback(
        request: DecisionRequest,
        future: asyncio.Future[DecisionResponse],
    ) -> None:
        print(f"GUI received request: '{request.message}' with args {request.kwargs}")
        # Simulate user interaction delay
        if request.message == "ask1":
            await asyncio.sleep(0.1)
            result = DecisionResponse.YES_TO_ALL if request.kwargs.get("all") else DecisionResponse.YES
            print(f"GUI responding {result} to '{request.message}'")
            future.set_result(result)
        if request.message == "ask2":
            await asyncio.sleep(0.1)
            print(f"GUI responding NO to '{request.message}'")
            future.set_result(DecisionResponse.NO)
        if request.message == "ask3":
            await asyncio.sleep(0.1)
            print(f"GUI responding YES to '{request.message}'")
            future.set_result(DecisionResponse.YES)


    await TaskScheduler.execute(gui_request_callback=gui_callback, tasks=tasks)

    assert actual_task_order == expected_task_order


#@task
def task3() -> Task:
    print("Task 2: running...")
    time.sleep(0.01)
    for i in range(3):
        yield task3()
    print("Task 2: completed.")



@pytest.mark.asyncio
async def test_task_scheduler_task_spawn() -> None:
    tasks = [
        #task3(),
        #task3(),
     ]

    async def gui_callback(
        request: DecisionRequest,
        future: asyncio.Future[DecisionResponse],
    ) -> None:
        print(f"GUI received request: '{request.message}' with args {request.kwargs}")

    await TaskScheduler.execute(gui_request_callback=gui_callback, tasks=tasks)
