from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Label, ProgressBar

from nova_navigator.scheduler import Job

from ..widgets.overlay_widget import OverlayWidget
from ..widgets.separator import Separator
from .job_registry import JobRegistry


class JobRow(Widget):
    """A single row in the jobs dialog representing one job."""

    DEFAULT_CSS = """
    JobRow {
        height: auto;
        padding: 0;
        margin: 0;
    }
    JobRow.-running {
        background: $panel;
        &:hover { background: $secondary; }
    }
    JobRow.-completed {
        background: $success 20%;
        &:hover { background: $success 40%; }
    }
    JobRow.-canceled {
        background: $error 15%;
        &:hover { background: $error 30%; }
    }
    JobRow.-failed {
        background: $error 20%;
        &:hover { background: $error 40%; }
    }
    JobRow .job-header {
        height: 1;
        align: left middle;
    }
    JobRow .toggle-icon {
        width: 2;
        height: 1;
    }
    JobRow .job-title {
        width: 1fr;
        height: 1;
    }
    JobRow .eta-label {
        width: 8;
        height: 1;
        content-align: right middle;
        color: $text-muted;
    }
    JobRow Button {
        width: 3;
        min-width: 3;
        max-width: 3;
        height: 1;
        border: none;
        padding: 0 0;
        margin-left: 2;
    }
    JobRow .job-body {
        height: auto;
        padding: 0 0 0 2;
    }
    JobRow .progress-row {
        height: 1;
        padding: 0 0 0 1;
    }
    JobRow .progress-row ProgressBar {
        width: 1fr;
        margin: 0;
    }
    JobRow .progress-row ProgressBar Bar {
        width: 1fr;
    }
    JobRow .pct-label {
        width: 5;
        content-align: right middle;
        padding: 0;
    }
    JobRow .step-spacer {
        width: 9;
    }
    JobRow .count-label {
        width: 9;
        content-align: right middle;
        padding: 0;
    }
    JobRow .step-bar Bar {
        color: $warning;
    }
    JobRow .error-msg {
        color: $error;
        padding: 0 1;
    }


    """

    _job: Job
    _on_action: Callable[[Job], None]
    _expanded: bool
    _step_bar: ProgressBar
    _overall_bar: ProgressBar
    _step_pct_label: Label
    _overall_count_label: Label
    _overall_pct_label: Label
    _step_row: Horizontal
    _overall_row: Horizontal
    _error_label: Label
    _toggle_label: Label
    _title_label: Label
    _eta_label: Label
    _action_button: Button

    def __init__(self, job: Job, on_action: Callable[[Job], None]) -> None:
        super().__init__()
        self._job = job
        self._on_action = on_action
        self._expanded = job.state == Job.State.RUNNING
        self._step_bar = ProgressBar(total=1, show_eta=False, show_percentage=False, classes="step-bar")
        self._overall_bar = ProgressBar(total=1, show_eta=False, show_percentage=False, classes="overall-bar")
        self._step_pct_label = Label("0%", classes="pct-label")
        self._overall_count_label = Label("0/0", classes="count-label")
        self._overall_pct_label = Label("0%", classes="pct-label")
        self._step_row = Horizontal(
            self._step_bar, Label("", classes="step-spacer"), self._step_pct_label, classes="progress-row"
        )
        self._overall_row = Horizontal(
            self._overall_bar, self._overall_count_label, self._overall_pct_label, classes="progress-row"
        )
        self._error_label = Label(job.error or "", classes="error-msg")
        self._toggle_label = Label(self._toggle_icon(), classes="toggle-icon")
        self._title_label = Label(self._display_title(job), classes="job-title")
        self._eta_label = Label("", classes="eta-label")
        self._action_button = Button(self._button_icon(job.state), id="action", compact=True)

    def _toggle_icon(self) -> str:
        return "▼" if self._expanded else "▶"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="job-header"):
            yield self._toggle_label
            yield self._title_label
            yield self._eta_label
            yield self._action_button
        with Vertical(classes="job-body"):
            yield self._step_row
            yield self._overall_row
            yield self._error_label

    def on_mount(self) -> None:
        self.query_one(".job-body").display = self._expanded
        self._error_label.display = self._job.state == Job.State.FAILED and bool(self._job.error)

    def on_click(self) -> None:
        self._expanded = not self._expanded
        self._toggle_label.update(self._toggle_icon())
        self.query_one(".job-body").display = self._expanded

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

    def refresh_job(self, job: Job) -> None:
        """Update this row in-place with current job state."""
        self._job = job
        self._title_label.update(self._display_title(job))
        self._action_button.label = self._button_icon(job.state)

        self.remove_class("-running", "-completed", "-canceled", "-failed")
        self.add_class(f"-{job.state.name.lower()}")

        progress = job.progress
        step_total = max(1, progress.step_total)
        overall_total = max(1, progress.total)
        overall_effective = min(overall_total, progress.completed + progress.step_completed / step_total)
        self._step_bar.update(total=step_total, progress=progress.step_completed)
        self._overall_bar.update(total=overall_total, progress=overall_effective)
        step_pct = int(progress.step_completed / step_total * 100)
        overall_pct = int(overall_effective / overall_total * 100)
        self._step_pct_label.update(f"{step_pct}%")
        self._overall_count_label.update(f"{progress.completed}/{overall_total}")
        self._overall_pct_label.update(f"{overall_pct}%")

        if job.state == Job.State.RUNNING:
            eta_secs: int | None = self._overall_bar._display_eta  # type: ignore[attr-defined]
            if eta_secs is None:
                self._eta_label.update("")
            else:
                m, s = divmod(eta_secs, 60)
                self._eta_label.update(f"{m}:{s:02d}")
        else:
            self._eta_label.update("")

        if job.state == Job.State.FAILED and job.error:
            self._error_label.update(job.error)
            self._error_label.display = True
        else:
            self._error_label.display = False

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
        padding: 0;
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
    _rules: dict[int, Separator]
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
        self._rules = {}
        self._no_jobs_label = None
        self._scroll = VerticalScroll()

    def compose(self) -> ComposeResult:
        yield self._scroll

    def on_mount(self) -> None:
        self.display = False
        self.set_interval(1.0, self._tick)

    async def _tick(self) -> None:
        self._registry.update()
        desired: list[Job] = self._registry.running_jobs + self._registry.finished_jobs
        desired_ids = {id(job) for job in desired}

        # mount new rows
        for job in desired:
            if id(job) not in self._rows:
                row = JobRow(job, self._handle_action)
                row.add_class(f"-{job.state.name.lower()}")
                rule = Separator()
                self._rows[id(job)] = row
                self._rules[id(job)] = rule
                await self._scroll.mount(row)
                await self._scroll.mount(rule)

        # refresh existing rows
        for job in desired:
            row = self._rows.get(id(job))
            if row is not None:
                row.refresh_job(job)

        # remove rows for jobs no longer in the registry
        stale = [jid for jid in self._rows if jid not in desired_ids]
        for jid in stale:
            self._rows.pop(jid).remove()
            rule = self._rules.pop(jid, None)
            if rule is not None:
                rule.remove()

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
            rule = self._rules.pop(id(job), None)
            if rule is not None:
                rule.remove()
