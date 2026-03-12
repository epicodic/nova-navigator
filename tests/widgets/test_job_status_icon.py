from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult

from nova_navigator.dialogs.job_registry import JobRegistry
from nova_navigator.icons import ico_
from nova_navigator.scheduler import Job
from nova_navigator.widgets.job_status_icon import JobStatusIcon, _State
from nova_widgets.icon import Icon


class _TestApp(App[None]):
    def __init__(self, widget: JobStatusIcon) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


def _make_job(state: Job.State) -> Job:
    """Return a mock Job with the given state."""
    job = MagicMock(spec=Job)
    job.state = state
    return job


@pytest.mark.asyncio
async def test_job_status_icon_starts_idle() -> None:
    registry = JobRegistry()
    icon = JobStatusIcon(registry=registry, action="show_processes")
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert icon._current_state == _State.IDLE


@pytest.mark.asyncio
async def test_job_status_icon_running_when_jobs_present() -> None:
    registry = JobRegistry()
    registry.add_job(_make_job(Job.State.RUNNING))
    icon = JobStatusIcon(registry=registry, action="show_processes")
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon._update()
        assert icon._current_state == _State.RUNNING


@pytest.mark.asyncio
async def test_job_status_icon_failed_when_failed_job_in_finished() -> None:
    registry = JobRegistry()
    registry._finished.appendleft(_make_job(Job.State.FAILED))
    icon = JobStatusIcon(registry=registry, action="show_processes")
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon._update()
        assert icon._current_state == _State.FAILED


@pytest.mark.asyncio
async def test_job_status_icon_failed_takes_priority_over_running() -> None:
    registry = JobRegistry()
    registry.add_job(_make_job(Job.State.RUNNING))
    registry._finished.appendleft(_make_job(Job.State.FAILED))
    icon = JobStatusIcon(registry=registry, action="show_processes")
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon._update()
        assert icon._current_state == _State.FAILED


@pytest.mark.asyncio
async def test_job_status_icon_returns_to_idle_after_failed_cleared() -> None:
    registry = JobRegistry()
    failed_job = _make_job(Job.State.FAILED)
    registry._finished.appendleft(failed_job)
    icon = JobStatusIcon(registry=registry, action="show_processes")
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon._update()
        assert icon._current_state == _State.FAILED

        registry.remove_job(failed_job)
        icon._update()
        assert icon._current_state == _State.IDLE


@pytest.mark.asyncio
async def test_job_status_icon_idle_glyph_restored_after_failed_cleared() -> None:
    """Verify the idle icon is displayed after a failed job is dismissed — not the failed icon."""
    registry = JobRegistry()
    failed_job = _make_job(Job.State.FAILED)
    registry._finished.appendleft(failed_job)

    icon = JobStatusIcon(registry=registry, action="show_processes")
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon._update()
        assert icon._current_state == _State.FAILED

        registry.remove_job(failed_job)
        icon._update()
        assert icon._current_state == _State.IDLE
        # The displayed glyph must be the idle icon (spinner_full), not a coloured failed frame
        idle_icon = ico_("job-spinner_full", default=Icon.of("●"))
        assert icon._animated_icon.renderable == idle_icon.markup
