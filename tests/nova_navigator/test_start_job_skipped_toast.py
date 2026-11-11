import asyncio
from functools import partial
from unittest.mock import MagicMock

import pytest

from nova_navigator.nova_navigator import NovaNavigator
from nova_navigator.scheduler import Job, ResponseRequest, TaskCancelled, TaskContext
from nova_widgets import Response


async def _no_gui(_request: ResponseRequest, future: asyncio.Future[Response]) -> None:
    future.set_result(Response.YES)


async def _skipping_task(ctx: TaskContext) -> None:
    ctx.report_skipped("a", "broken link")
    ctx.report_skipped("b", "link loop")
    ctx.report_skipped("c", "broken link")


async def _quiet_task(_ctx: TaskContext) -> None:
    return None


async def _skip_then_cancel_task(ctx: TaskContext) -> None:
    ctx.report_skipped("a", "broken link")
    ctx.report_skipped("b", "link loop")
    raise TaskCancelled


def _fake_app() -> MagicMock:
    app = MagicMock()
    app.request_callback = _no_gui
    app._notify_skipped = partial(NovaNavigator._notify_skipped, app)
    return app


@pytest.mark.asyncio
async def test_job_with_three_skipped_links_notifies_once() -> None:
    app = _fake_app()
    await NovaNavigator.start_job(app, Job("Copy", _skipping_task))
    assert app.notify.call_count == 1
    message = app.notify.call_args.args[0]
    assert "3" in message
    assert "link" in message
    assert app.notify.call_args.kwargs["severity"] == "warning"
    assert app.notify.call_args.kwargs["title"] == "Copy"


@pytest.mark.asyncio
async def test_job_without_skips_does_not_notify() -> None:
    app = _fake_app()
    await NovaNavigator.start_job(app, Job("Copy", _quiet_task))
    assert app.notify.call_count == 0


@pytest.mark.asyncio
async def test_canceled_job_with_skips_still_notifies_once() -> None:
    app = _fake_app()
    job = Job("Copy", _skip_then_cancel_task)
    await NovaNavigator.start_job(app, job)
    assert job.state == Job.State.CANCELED
    assert app.notify.call_count == 1
    assert "2" in app.notify.call_args.args[0]
