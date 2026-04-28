import asyncio

import pytest

from nova_navigator.decision import Decision
from nova_navigator.scheduler import DecisionRequest, Job, TaskContext


async def _failing_task(_ctx: TaskContext) -> None:
    raise RuntimeError("something went wrong")


async def _ok_task(ctx: TaskContext) -> None:
    ctx.status.set_completed()


async def _no_gui(_request: DecisionRequest, future: asyncio.Future[Decision]) -> None:
    future.set_result(Decision.YES)


@pytest.mark.asyncio
async def test_job_failed_state_on_exception() -> None:
    """A task that raises transitions the job to FAILED."""
    job = Job("test", _failing_task)
    await job.start(_no_gui)
    assert job.state == Job.State.FAILED


@pytest.mark.asyncio
async def test_job_error_message_stored() -> None:
    """The exception message is stored in job.error."""
    job = Job("test", _failing_task)
    await job.start(_no_gui)
    assert job.error == "something went wrong"


@pytest.mark.asyncio
async def test_job_error_is_none_on_success() -> None:
    """error property is None when the job completes successfully."""
    job = Job("test", _ok_task)
    await job.start(_no_gui)
    assert job.state == Job.State.COMPLETED
    assert job.error is None


def test_job_error_is_none_before_start() -> None:
    """error property is None on a freshly constructed Job."""
    job = Job("test", _ok_task)
    assert job.error is None


@pytest.mark.asyncio
async def test_job_canceled_state_not_failed() -> None:
    """A cancelled task transitions to CANCELED, not FAILED."""

    async def _cancellable_task(ctx: TaskContext) -> None:
        ctx.status.check_cancelled()

    job = Job("test", _cancellable_task)
    job.cancel()
    await job.start(_no_gui)
    assert job.state == Job.State.CANCELED
    assert job.error is None
