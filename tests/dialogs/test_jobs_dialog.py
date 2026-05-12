from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Label

from nova_navigator.dialogs.job_registry import JobRegistry
from nova_navigator.dialogs.jobs_dialog import JobRow, JobsDialog
from nova_navigator.scheduler import Job
from nova_navigator.scheduler.context import Progress


def _make_progress(
    total: int = 10,
    completed: int = 5,
    step_total: int = 100,
    step_completed: int = 50,
) -> Progress:
    return Progress(total=total, completed=completed, step_total=step_total, step_completed=step_completed)


def _make_job(
    title: str = "test",
    state: Job.State = Job.State.RUNNING,
    error: str | None = None,
) -> MagicMock:
    job = MagicMock(spec=Job)
    job.title = title
    job.state = state
    job.error = error
    job.progress = _make_progress()
    return job


# ── helpers ───────────────────────────────────────────────────────────────────


class _RowApp(App[None]):
    def __init__(self, row: JobRow) -> None:
        super().__init__()
        self._row = row

    def compose(self) -> ComposeResult:
        yield self._row


class _DialogApp(App[None]):
    def __init__(self, dialog: JobsDialog) -> None:
        super().__init__()
        self._dialog = dialog

    def compose(self) -> ComposeResult:
        yield self._dialog


# ── JobRow static helpers ─────────────────────────────────────────────────────


def test_display_title_canceled() -> None:
    assert JobRow._display_title(_make_job(title="Copy", state=Job.State.CANCELED)) == "Copy (canceled)"


def test_display_title_failed() -> None:
    assert JobRow._display_title(_make_job(title="Copy", state=Job.State.FAILED)) == "Copy (failed)"


def test_display_title_running() -> None:
    assert JobRow._display_title(_make_job(title="Copy", state=Job.State.RUNNING)) == "Copy"


def test_button_icon_running() -> None:
    assert JobRow._button_icon(Job.State.RUNNING) == "✕"


def test_button_icon_completed() -> None:
    assert JobRow._button_icon(Job.State.COMPLETED) == "✓"


def test_button_icon_failed() -> None:
    assert JobRow._button_icon(Job.State.FAILED) == "✗"


# ── JobRow widget ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_row_running_is_expanded_on_mount() -> None:
    job = _make_job(state=Job.State.RUNNING)
    row = JobRow(job, lambda j: None)
    async with _RowApp(row).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert row.query_one(".job-body").display is True


@pytest.mark.asyncio
async def test_job_row_completed_is_collapsed_on_mount() -> None:
    job = _make_job(state=Job.State.COMPLETED)
    row = JobRow(job, lambda j: None)
    async with _RowApp(row).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert row.query_one(".job-body").display is False


@pytest.mark.asyncio
async def test_job_row_click_toggles_body() -> None:
    job = _make_job(state=Job.State.RUNNING)
    row = JobRow(job, lambda j: None)
    async with _RowApp(row).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        body = row.query_one(".job-body")
        assert body.display is True
        await pilot.click(row.query_one(".toggle-icon"))
        await pilot.pause()
        assert body.display is False
        await pilot.click(row.query_one(".toggle-icon"))
        await pilot.pause()
        assert body.display is True


@pytest.mark.asyncio
async def test_job_row_error_label_hidden_when_running() -> None:
    job = _make_job(state=Job.State.RUNNING, error=None)
    row = JobRow(job, lambda j: None)
    async with _RowApp(row).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert row.query_one(".error-msg").display is False


@pytest.mark.asyncio
async def test_job_row_error_label_shown_for_failed_with_error() -> None:
    job = _make_job(state=Job.State.FAILED, error="disk full")
    row = JobRow(job, lambda j: None)
    async with _RowApp(row).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert row.query_one(".error-msg").display is True


@pytest.mark.asyncio
async def test_job_row_error_label_hidden_for_failed_without_error() -> None:
    job = _make_job(state=Job.State.FAILED, error=None)
    row = JobRow(job, lambda j: None)
    async with _RowApp(row).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert row.query_one(".error-msg").display is False


@pytest.mark.asyncio
async def test_refresh_job_updates_css_class() -> None:
    job = _make_job(state=Job.State.RUNNING)
    row = JobRow(job, lambda j: None)
    async with _RowApp(row).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        job.state = Job.State.COMPLETED
        row.refresh_job(job)
        await pilot.pause()
        assert row.has_class("-completed")
        assert not row.has_class("-running")


@pytest.mark.asyncio
async def test_refresh_job_shows_error_label_for_failed() -> None:
    job = _make_job(state=Job.State.RUNNING)
    row = JobRow(job, lambda j: None)
    async with _RowApp(row).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        job.state = Job.State.FAILED
        job.error = "something went wrong"
        row.refresh_job(job)
        await pilot.pause()
        assert row.query_one(".error-msg").display is True


@pytest.mark.asyncio
async def test_refresh_job_hides_error_label_when_no_error() -> None:
    job = _make_job(state=Job.State.FAILED, error="oops")
    row = JobRow(job, lambda j: None)
    async with _RowApp(row).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        job.state = Job.State.COMPLETED
        job.error = None
        row.refresh_job(job)
        await pilot.pause()
        assert row.query_one(".error-msg").display is False


@pytest.mark.asyncio
async def test_refresh_job_updates_progress_labels() -> None:
    job = _make_job(state=Job.State.RUNNING)
    job.progress = Progress(total=10, completed=3, step_total=100, step_completed=40)
    row = JobRow(job, lambda j: None)
    async with _RowApp(row).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        # verify refresh_job runs without error and updates bar values
        row.refresh_job(job)
        await pilot.pause()
        assert row._overall_bar.total == 10


@pytest.mark.asyncio
async def test_button_press_calls_on_action() -> None:
    job = _make_job(state=Job.State.RUNNING)
    received: list[object] = []
    row = JobRow(job, received.append)
    async with _RowApp(row).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click(row.query_one(Button))
        await pilot.pause()
        assert received == [job]


# ── JobsDialog ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jobs_dialog_hidden_on_mount() -> None:
    registry = JobRegistry()
    dialog = JobsDialog((0, 0), registry)
    async with _DialogApp(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert dialog.display is False


@pytest.mark.asyncio
async def test_jobs_dialog_show_makes_visible() -> None:
    registry = JobRegistry()
    dialog = JobsDialog((0, 0), registry)
    async with _DialogApp(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        dialog.show()
        await pilot.pause()
        assert dialog.display is True


@pytest.mark.asyncio
async def test_jobs_dialog_show_updates_position() -> None:
    registry = JobRegistry()
    dialog = JobsDialog((0, 0), registry)
    async with _DialogApp(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        dialog.show()
        await pilot.pause()
        assert dialog.offset.x >= 0


@pytest.mark.asyncio
async def test_tick_mounts_row_for_running_job() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    dialog = JobsDialog((0, 0), registry)
    async with _DialogApp(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await dialog._tick()
        await pilot.pause()
        assert len(dialog._rows) == 1


@pytest.mark.asyncio
async def test_tick_shows_no_jobs_label_when_empty() -> None:
    registry = JobRegistry()
    dialog = JobsDialog((0, 0), registry)
    async with _DialogApp(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await dialog._tick()
        await pilot.pause()
        assert dialog._no_jobs_label is not None
        assert dialog.query_one("#no-jobs", Label) is not None


@pytest.mark.asyncio
async def test_tick_removes_no_jobs_label_when_job_arrives() -> None:
    registry = JobRegistry()
    dialog = JobsDialog((0, 0), registry)
    async with _DialogApp(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await dialog._tick()
        await pilot.pause()
        assert dialog._no_jobs_label is not None

        job = _make_job(state=Job.State.RUNNING)
        registry.add_job(job)
        await dialog._tick()
        await pilot.pause()
        assert dialog._no_jobs_label is None


@pytest.mark.asyncio
async def test_tick_refreshes_existing_row() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    dialog = JobsDialog((0, 0), registry)
    async with _DialogApp(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await dialog._tick()
        await pilot.pause()
        assert len(dialog._rows) == 1
        # second tick should refresh (not duplicate) the row
        await dialog._tick()
        await pilot.pause()
        assert len(dialog._rows) == 1


@pytest.mark.asyncio
async def test_tick_removes_stale_row() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    dialog = JobsDialog((0, 0), registry)
    async with _DialogApp(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await dialog._tick()
        await pilot.pause()
        assert len(dialog._rows) == 1

        # move job to finished, then remove it from registry
        job.state = Job.State.COMPLETED
        await dialog._tick()  # registry.update() moves it to finished
        await pilot.pause()
        registry.remove_job(job)
        await dialog._tick()  # job no longer in desired → row removed
        await pilot.pause()
        assert len(dialog._rows) == 0


@pytest.mark.asyncio
async def test_handle_action_cancels_running_job() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    dialog = JobsDialog((0, 0), registry)
    async with _DialogApp(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        dialog._handle_action(job)
        job.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_handle_action_removes_finished_job_row() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    dialog = JobsDialog((0, 0), registry)
    async with _DialogApp(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await dialog._tick()  # row mounted
        await pilot.pause()
        assert len(dialog._rows) == 1

        job.state = Job.State.COMPLETED
        await dialog._tick()  # row refreshed, job now in finished
        await pilot.pause()

        dialog._handle_action(job)
        await pilot.pause()
        assert id(job) not in dialog._rows
