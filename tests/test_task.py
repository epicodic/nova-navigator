import asyncio
import time

import pytest

from nova_navigator.task import DecisionRequest, DecisionResponse, Task, TaskScheduler


def task1(id: int, input: str) -> Task:
    if input == "ask":
        response = yield DecisionRequest("Proceed with task 1?", id=id, input=input)
        if response.is_no:
            return

    print(f"Task 1 ({id}): running...")
    time.sleep(1)
    print(f"Task 1 ({id}): completed.")


# def task2() -> Task:
#    yield DecisionRequest("Task 2 completed. Proceed with task 3?")


async def gui_callback(
    request: DecisionRequest,
    future: asyncio.Future[DecisionResponse],
) -> None:
    print(f"GUI received request: {request.message} with args {request.kwargs}")
    # Simulate user interaction delay
    await asyncio.sleep(2.0)
    future.set_result(DecisionResponse.YES)


@pytest.mark.asyncio
async def test_task_scheduler() -> None:
    loop = asyncio.get_running_loop()
    scheduler = TaskScheduler(gui_request_callback=gui_callback, event_loop=loop)

    # scheduler.run_tasks([task1(1, "ask"), task1(2, "no_ask")])

    await asyncio.create_task(asyncio.to_thread(scheduler.run_tasks, [task1(1, "ask"), task1(2, "no_ask")]))
