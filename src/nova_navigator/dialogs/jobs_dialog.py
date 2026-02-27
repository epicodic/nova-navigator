from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Label, ProgressBar

from nova_navigator.scheduler import Job

from ..widgets.overlay_widget import OverlayWidget
from .job_registry import JobRegistry


class JobRow(Widget):
    """A single row in the jobs dialog representing one job."""

    DEFAULT_CSS = """
    JobRow {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
    }
    JobRow.-running {
        background: $panel;
    }
    JobRow.-completed {
        background: $success 20%;
    }
    JobRow.-canceled {
        background: $error 15%;
    }
    JobRow.-failed {
        background: $error 20%;
    }
    JobRow Horizontal {
        height: 1;
        align: left middle;
    }
    JobRow Label {
        width: 1fr;
        height: 1;
    }
    JobRow Button {
        width: 3;
        min-width: 3;
        max-width: 3;
        height: 1;
        border: none;
        padding: 0 0;
    }
    """

    _job: Job
    _on_action: Callable[[Job], None]
    _step_bar: ProgressBar
    _overall_bar: ProgressBar
    _title_label: Label
    _action_button: Button

    def __init__(self, job: Job, on_action: Callable[[Job], None]) -> None:
        super().__init__()
        self._job = job
        self._on_action = on_action
        self._step_bar = ProgressBar(total=1, show_eta=False)
        self._overall_bar = ProgressBar(total=1, show_eta=False)
        self._title_label = Label(self._display_title(job))
        self._action_button = Button(self._button_icon(job.state), id="action")

    @staticmethod
    def _display_title(job: Job) -> str:
        match job.state:
            case Job.State.CANCELED:
                return f"{job.title} (canceled)"
            case Job.State.FAILED:
                return f"{job.title} (failed)"
            case _:
                return job.title

    @staticmethod
    def _button_icon(state: Job.State) -> str:
        return "✕" if state == Job.State.RUNNING else ("✓" if state == Job.State.COMPLETED else "✗")

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield self._title_label
            yield self._action_button
        if self._job.state == Job.State.RUNNING:
            yield self._step_bar
            yield self._overall_bar

    def refresh_job(self, job: Job) -> None:
        """Update this row in-place with current job state."""
        self._job = job
        self._title_label.update(self._display_title(job))
        self._action_button.label = self._button_icon(job.state)

        # apply CSS class for coloring
        self.remove_class("-running", "-completed", "-canceled", "-failed")
        self.add_class(f"-{job.state.name.lower()}")

        # update or remove progress bars based on state
        if job.state == Job.State.RUNNING:
            progress = job.progress
            step_total = max(1, progress.step_total)
            overall_total = max(1, progress.total)
            self._step_bar.update(total=step_total, progress=progress.step_completed)
            self._overall_bar.update(total=overall_total, progress=progress.completed)
        else:
            if self._step_bar.is_attached:
                self._step_bar.remove()
            if self._overall_bar.is_attached:
                self._overall_bar.remove()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._on_action(self._job)


class JobsDialog(OverlayWidget, can_focus=True):
    """Floating overlay that shows running and finished jobs."""

    DEFAULT_CSS = """
    JobsDialog {
        width: 62;
        height: auto;
        max-height: 30;
    }
    JobsDialog VerticalScroll {
        height: auto;
        max-height: 28;
    }
    JobsDialog #no-jobs {
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
        padding: 1;
    }
    """

    _registry: JobRegistry
    _rows: dict[int, JobRow]
    _scroll: VerticalScroll
    _no_jobs_label: Label | None

    def __init__(self, position: tuple[int, int], registry: JobRegistry) -> None:
        super().__init__(
            "Jobs",
            position,
            close_on_escape=True,
            close_on_blur=False,
            close_action=OverlayWidget.CloseAction.HIDE,
        )
        self._registry = registry
        self._rows = {}
        self._no_jobs_label = None
        self._scroll = VerticalScroll()

    def compose(self) -> ComposeResult:
        yield self._scroll

    def on_mount(self) -> None:
        self.display = False
        self.set_interval(0.5, self._tick)

    async def _tick(self) -> None:
        self._registry.update()
        desired: list[Job] = self._registry.running_jobs + self._registry.finished_jobs
        desired_ids = {id(job) for job in desired}

        # mount new rows
        for job in desired:
            if id(job) not in self._rows:
                row = JobRow(job, self._handle_action)
                row.add_class(f"-{job.state.name.lower()}")
                self._rows[id(job)] = row
                await self._scroll.mount(row)

        # refresh existing rows
        for job in desired:
            row = self._rows.get(id(job))
            if row is not None:
                row.refresh_job(job)

        # remove rows for jobs no longer in the registry
        stale = [jid for jid in self._rows if jid not in desired_ids]
        for jid in stale:
            self._rows.pop(jid).remove()

        # empty state label
        if not desired:
            if self._no_jobs_label is None:
                self._no_jobs_label = Label("No jobs", id="no-jobs")
                await self._scroll.mount(self._no_jobs_label)
        else:
            if self._no_jobs_label is not None:
                self._no_jobs_label.remove()
                self._no_jobs_label = None

    def _handle_action(self, job: Job) -> None:
        if job.state == Job.State.RUNNING:
            job.cancel()
        else:
            self._registry.remove_job(job)
            row = self._rows.pop(id(job), None)
            if row is not None:
                row.remove()
