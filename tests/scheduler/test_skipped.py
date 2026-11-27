import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from nova_navigator.scheduler import Job, ResponseRequest, SkippedItem, TaskContext, TaskStatus
from nova_widgets import Response


async def _no_gui(_request: ResponseRequest, future: asyncio.Future[Response]) -> None:
    future.set_result(Response.YES)


def _status() -> TaskStatus:
    return TaskStatus(threading.Event(), MagicMock())


def test_report_skipped_keeps_report_order() -> None:
    status = _status()
    status.report_skipped("a", "first")
    status.report_skipped("b", "second")
    status.report_skipped("c", "third")
    assert status.skipped == (SkippedItem("a", "first"), SkippedItem("b", "second"), SkippedItem("c", "third"))


def test_report_skipped_concurrent_threads_lose_nothing() -> None:
    threads_count, calls = 8, 250
    status = _status()
    barrier = threading.Barrier(threads_count)

    def work(n: int) -> None:
        barrier.wait()
        for i in range(calls):
            status.report_skipped(f"{n}:{i}", "reason")

    threads = [threading.Thread(target=work, args=(n,)) for n in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    skipped = status.skipped
    assert len(skipped) == threads_count * calls
    for n in range(threads_count):
        own = [int(s.item.split(":")[1]) for s in skipped if s.item.startswith(f"{n}:")]
        assert own == list(range(calls))


@pytest.mark.asyncio
async def test_subtask_skips_land_on_parent_job() -> None:
    async def child(ctx: TaskContext) -> None:
        ctx.report_skipped("child-item", "child-reason")

    async def parent(ctx: TaskContext) -> None:
        ctx.report_skipped("parent-item", "parent-reason")
        await ctx.subtask(child(ctx))
        ctx.status.set_completed()

    job = Job("test", parent)
    await job.start(_no_gui)
    assert job.skipped == (SkippedItem("parent-item", "parent-reason"), SkippedItem("child-item", "child-reason"))


@pytest.mark.asyncio
async def test_skips_before_cancel_are_kept() -> None:
    async def task(ctx: TaskContext) -> None:
        ctx.report_skipped("x", "r1")
        ctx.report_skipped("y", "r2")
        job.cancel()
        ctx.status.check_cancelled()

    job = Job("test", task)
    await job.start(_no_gui)
    assert job.state == Job.State.CANCELED
    assert len(job.skipped) == 2


@pytest.mark.asyncio
async def test_job_without_skips_has_empty_list() -> None:
    async def task(ctx: TaskContext) -> None:
        ctx.status.set_completed()

    job = Job("test", task)
    assert job.skipped == ()
    await job.start(_no_gui)
    assert job.skipped == ()


@pytest.mark.asyncio
async def test_report_skipped_does_not_complete_job() -> None:
    callback = MagicMock()
    status = TaskStatus(threading.Event(), callback)
    status.report_skipped("a", "b")
    callback.assert_not_called()

    states: list[Job.State] = []

    async def task(ctx: TaskContext) -> None:
        ctx.report_skipped("a", "b")
        states.append(job.state)
        ctx.status.set_completed()

    job = Job("test", task)
    await job.start(_no_gui)
    assert states == [Job.State.RUNNING]


def test_skipped_property_returns_snapshot() -> None:
    status = _status()
    status.report_skipped("a", "r")
    snapshot = status.skipped
    status.report_skipped("b", "r")
    assert isinstance(snapshot, tuple)
    assert snapshot == (SkippedItem("a", "r"),)
    assert len(status.skipped) == 2
