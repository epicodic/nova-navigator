from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult

from nova_navigator.dialogs.jobs_dialog import JobListView, JobsDialog, _generate_progress_bar_segments
from nova_navigator.scheduler import Job
from nova_navigator.scheduler.context import Progress
from nova_navigator.scheduler.skipped import SkippedItem


def _make_progress(
    total: int = 10,
    completed: int = 5,
    step_total: int = 100,
    step_completed: int = 50,
    current_item: str = "file.txt",
) -> Progress:
    return Progress(
        total=total,
        completed=completed,
        step_total=step_total,
        step_completed=step_completed,
        current_item=current_item,
    )


def _make_job(
    title: str = "Copy",
    state: Job.State = Job.State.RUNNING,
    error: str | None = None,
    progress: Progress | None = None,
    skipped: tuple[SkippedItem, ...] = (),
) -> MagicMock:
    job = MagicMock(spec=Job)
    job.title = title
    job.state = state
    job.error = error
    job.progress = progress if progress is not None else _make_progress()
    job.skipped = skipped
    return job


class _ListViewApp(App[None]):
    def __init__(self, view: JobListView) -> None:
        super().__init__()
        self._view = view

    def compose(self) -> ComposeResult:
        yield self._view


# ── virtual_size ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_jobs_updates_virtual_size() -> None:
    view = JobListView()
    app = _ListViewApp(view)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        jobs = [_make_job(), _make_job()]
        view.set_jobs(jobs)
        await pilot.pause()
        assert view.virtual_size.height == 2 * JobListView.ITEM_HEIGHT


@pytest.mark.asyncio
async def test_set_jobs_empty_virtual_size() -> None:
    view = JobListView()
    app = _ListViewApp(view)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([])
        await pilot.pause()
        # at least 1 line for the "no jobs" message
        assert view.virtual_size.height >= 1


# ── render output ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_render_running_job_shows_title() -> None:
    job = _make_job(title="Copy files", state=Job.State.RUNNING)
    view = JobListView()
    app = _ListViewApp(view)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([job])
        await pilot.pause()
        strip = view.render_line(0)
        assert "Copy files" in strip.text


@pytest.mark.asyncio
async def test_render_running_job_shows_progress_percentage() -> None:
    progress = _make_progress(total=10, completed=5, step_total=100, step_completed=50)
    job = _make_job(state=Job.State.RUNNING, progress=progress)
    view = JobListView()
    app = _ListViewApp(view)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([job])
        await pilot.pause()
        # line 2 = step bar (50%)
        step_strip = view.render_line(2)
        assert "50%" in step_strip.text
        # line 3 = overall bar: bar shows (5 + 0.5) / 10 = 55%, pct matches bar
        overall_strip = view.render_line(3)
        assert "55%" in overall_strip.text


@pytest.mark.asyncio
async def test_render_running_job_shows_current_item() -> None:
    progress = _make_progress(current_item="important.txt")
    job = _make_job(state=Job.State.RUNNING, progress=progress)
    view = JobListView()
    app = _ListViewApp(view)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([job])
        await pilot.pause()
        strip = view.render_line(1)
        assert "important.txt" in strip.text


@pytest.mark.asyncio
async def test_render_failed_job_shows_error() -> None:
    job = _make_job(state=Job.State.FAILED, error="Permission denied")
    view = JobListView()
    app = _ListViewApp(view)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([job])
        await pilot.pause()
        strip = view.render_line(1)
        assert "Permission denied" in strip.text


@pytest.mark.asyncio
async def test_render_empty_shows_no_jobs_text() -> None:
    view = JobListView()
    app = _ListViewApp(view)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([])
        await pilot.pause()
        strip = view.render_line(0)
        assert "No jobs" in strip.text


# ── skipped section ───────────────────────────────────────────────────────────


def _all_lines(view: JobListView) -> list[str]:
    return [view.render_line(y).text for y in range(view.virtual_size.height)]


def _skips(count: int, reason: str = "broken link") -> tuple[SkippedItem, ...]:
    return tuple(SkippedItem(f"/src/file{i}", reason) for i in range(count))


@pytest.mark.asyncio
async def test_finished_job_renders_each_skipped_path_with_reason_in_order() -> None:
    skipped = (SkippedItem("/b/second", "link loop"), SkippedItem("/a/first", "broken link"))
    job = _make_job(state=Job.State.COMPLETED, skipped=skipped)
    view = JobListView()
    async with _ListViewApp(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([job])
        await pilot.pause()
        lines = _all_lines(view)
        header = next(i for i, line in enumerate(lines) if "Skipped (2):" in line)
        assert header == 4
        assert "/b/second" in lines[header + 1]
        assert "link loop" in lines[header + 1]
        assert "/a/first" in lines[header + 2]
        assert "broken link" in lines[header + 2]
        assert lines[header + 3].startswith("─")


@pytest.mark.asyncio
async def test_finished_job_lists_folder_exists_reason() -> None:
    job = _make_job(state=Job.State.COMPLETED, skipped=(SkippedItem("/src/x", "folder exists"),))
    view = JobListView()
    async with _ListViewApp(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([job])
        await pilot.pause()
        lines = _all_lines(view)
        header = next(i for i, line in enumerate(lines) if "Skipped (1):" in line)
        assert "/src/x" in lines[header + 1]
        assert "folder exists" in lines[header + 1]


@pytest.mark.asyncio
async def test_job_without_skips_has_no_skipped_section() -> None:
    job = _make_job(state=Job.State.COMPLETED)
    view = JobListView()
    async with _ListViewApp(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([job])
        await pilot.pause()
        assert not any("Skipped" in line for line in _all_lines(view))
        assert view.virtual_size.height == JobListView.ITEM_HEIGHT


@pytest.mark.asyncio
async def test_running_job_does_not_show_skipped_section() -> None:
    job = _make_job(state=Job.State.RUNNING, skipped=_skips(2))
    view = JobListView()
    async with _ListViewApp(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([job])
        await pilot.pause()
        assert not any("Skipped" in line for line in _all_lines(view))
        assert view.virtual_size.height == JobListView.ITEM_HEIGHT


@pytest.mark.asyncio
async def test_set_jobs_virtual_size_includes_skipped_lines() -> None:
    jobs = [_make_job(state=Job.State.COMPLETED, skipped=_skips(3)), _make_job(state=Job.State.COMPLETED)]
    view = JobListView()
    async with _ListViewApp(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs(jobs)
        await pilot.pause()
        assert view.virtual_size.height == (JobListView.ITEM_HEIGHT + 1 + 3) + JobListView.ITEM_HEIGHT


@pytest.mark.asyncio
async def test_long_skipped_list_is_scrollable_to_last_entry() -> None:
    job = _make_job(state=Job.State.COMPLETED, skipped=_skips(200))
    view = JobListView()
    async with _ListViewApp(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([job])
        await pilot.pause()
        assert view.virtual_size.height > view.size.height
        view.scroll_end(animate=False)
        await pilot.pause()
        rendered = [view.render_line(y).text for y in range(view.size.height)]
        assert any("/src/file199" in line for line in rendered)


@pytest.mark.asyncio
async def test_long_skipped_path_is_truncated_left_reason_kept() -> None:
    long_path = "/very/long/" + "d" * 120 + "/tail_name.txt"
    job = _make_job(state=Job.State.COMPLETED, skipped=(SkippedItem(long_path, "link loop"),))
    view = JobListView()
    async with _ListViewApp(view).run_test(size=(60, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([job])
        await pilot.pause()
        line = _all_lines(view)[5]
        assert "…" in line
        assert "tail_name.txt" in line
        assert "link loop" in line
        assert "/very/long/" not in line
        assert len(line) == view.size.width


@pytest.mark.asyncio
async def test_click_action_button_on_job_after_skipped_section() -> None:
    first = _make_job(title="First", state=Job.State.COMPLETED, skipped=_skips(3))
    second = _make_job(title="Second", state=Job.State.COMPLETED)
    clicked: list[Job] = []
    view = JobListView(on_action=clicked.append)
    async with _ListViewApp(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([first, second])
        await pilot.pause()
        second_top = JobListView.ITEM_HEIGHT + 1 + 3
        x = view.size.width - JobListView.RIGHT_PADDING - 2
        await pilot.hover(view, offset=(x, second_top + 2))
        assert view._hovered_job_index == 1
        assert not view._hovered_btn
        await pilot.hover(view, offset=(x, second_top))
        assert view._hovered_btn
        await pilot.click(view, offset=(x, second_top))
        await pilot.pause()
        assert clicked == [second]


# ── action button metadata ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_action_button_segment_has_metadata() -> None:
    job = _make_job(state=Job.State.RUNNING)
    view = JobListView()
    app = _ListViewApp(view)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.set_jobs([job])
        await pilot.pause()
        # Line 0: action button is in the rightmost segments
        strip = view.render_line(0)
        segments = list(strip)
        has_action_meta = any(seg.style and seg.style.meta.get("action_button") for seg in segments)
        assert has_action_meta


# ── JobsDialog integration ────────────────────────────────────────────────────


class _DialogApp(App[None]):
    def __init__(self, dialog: JobsDialog) -> None:
        super().__init__()
        self._dialog = dialog

    def compose(self) -> ComposeResult:
        yield self._dialog


@pytest.mark.asyncio
async def test_jobs_dialog_shows_job_after_tick() -> None:
    from nova_navigator.dialogs.job_registry import JobRegistry

    registry = MagicMock(spec=JobRegistry)
    job = _make_job(title="My Job", state=Job.State.RUNNING)
    registry.running_jobs = [job]
    registry.finished_jobs = []

    dialog = JobsDialog(position=(0, 0), registry=registry)
    app = _DialogApp(dialog)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        dialog.show()
        await pilot.pause(delay=0.1)
        job_list = dialog.query_one(JobListView)
        assert len(job_list._jobs) == 1
        assert job_list._jobs[0].title == "My Job"


# ── _generate_bar_segments ───────────────────────────────────────────────────


def test_bar_segments_empty() -> None:
    fill, muted = _generate_progress_bar_segments(0.0, 8)
    assert fill == ""
    assert muted == "━━━━━━━━"


def test_bar_segments_full() -> None:
    fill, muted = _generate_progress_bar_segments(1.0, 8)
    assert fill == "━━━━━━━━"
    assert muted == ""


def test_bar_segments_half_cell() -> None:
    # 0.5 cells filled out of 8
    fill, muted = _generate_progress_bar_segments(0.5 / 8, 8)
    assert fill == "╸"
    assert muted == "━━━━━━━"


def test_bar_segments_integer_cell() -> None:
    # 1.0 cells filled out of 8
    fill, muted = _generate_progress_bar_segments(1 / 8, 8)
    assert fill == "━"
    assert muted == "╺━━━━━━"


def test_bar_segments_one_and_half_cells() -> None:
    # 1.5 cells filled out of 8
    fill, muted = _generate_progress_bar_segments(1.5 / 8, 8)
    assert fill == "━╸"
    assert muted == "━━━━━━"


def test_bar_segments_two_cells() -> None:
    # 2.0 cells filled out of 8
    fill, muted = _generate_progress_bar_segments(2 / 8, 8)
    assert fill == "━━"
    assert muted == "╺━━━━━"


def test_bar_segments_total_width() -> None:
    # len(fill) + len(muted) must always equal width
    for steps in range(17):
        ratio = steps / 16
        fill, muted = _generate_progress_bar_segments(ratio, 8)
        assert len(fill) + len(muted) == 8, f"width mismatch at step {steps}/16"
