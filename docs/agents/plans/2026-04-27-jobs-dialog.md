# Jobs Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a floating `JobsDialog` overlay that shows running, completed, and failed jobs with live progress bars and cancel/remove buttons, backed by a `JobRegistry` on `MainScreen`.

**Architecture:** `Job` gains a `FAILED` state and stores exception messages. A plain `JobRegistry` class manages running/finished job lists and is polled by the dialog's timer tick. `JobsDialog` (an `OverlayWidget`) reconciles `JobRow` widgets on each 0.5 s tick.

**Tech Stack:** Python 3.12, pytest, Textual

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Spec:** `docs/agents/specs/2026-04-27-jobs-dialog-design.md` — read before implementing

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/nova_navigator/scheduler/job.py` | Modify | Add `FAILED` state, `_error` field, `error` property, wrap `start()` |
| `src/nova_navigator/dialogs/job_registry.py` | Create | `JobRegistry` + `MAX_FINISHED_JOBS` |
| `src/nova_navigator/dialogs/jobs_dialog.py` | Create | `JobsDialog` + `JobRow` widgets |
| `src/nova_navigator/dialogs/__init__.py` | Modify | Export `JobRegistry`, `JobsDialog` |
| `src/nova_navigator/main.py` | Modify | Add registry, mount dialog, wire `add_job`, implement `action_show_processes` |
| `tests/scheduler/test_job_failed_state.py` | Create | Tests for `FAILED` state on `Job` |
| `tests/dialogs/test_job_registry.py` | Create | Tests for `JobRegistry` |

---

## Task 1: Add `FAILED` state to `Job`

**Files:**
- Modify: `src/nova_navigator/scheduler/job.py`
- Test: `tests/scheduler/test_job_failed_state.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/scheduler/test_job_failed_state.py`:

```python
import pytest

from nova_navigator.scheduler import Job, TaskContext


async def _failing_task(ctx: TaskContext) -> None:
    raise RuntimeError("something went wrong")


async def _ok_task(ctx: TaskContext) -> None:
    ctx.status.set_completed()


async def _no_gui(request, future):  # type: ignore[no-untyped-def]
    future.set_result(None)


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
```

- [ ] **Step 2: Run to confirm tests fail**

```
uv run pytest tests/scheduler/test_job_failed_state.py -v
```
Expected: FAIL — `FAILED` state and `error` property do not exist yet.

- [ ] **Step 3: Implement the changes in `job.py`**

Replace the contents of `src/nova_navigator/scheduler/job.py` with:

```python
import threading
from collections.abc import Awaitable, Callable
from enum import Enum, auto
from typing import Any

from .context import GuiRequestCallback, Progress, TaskContext, TaskStatus
from .scheduler import AsyncTaskScheduler


class Job:
    """Represents a long-running job whose progress can be tracked and that can be cancelled.

    The task function receives a :class:`TaskContext` as its first argument and is
    executed by :class:`AsyncTaskScheduler` in a worker thread.
    """

    class State(Enum):
        INITIALIZED = auto()
        RUNNING = auto()
        COMPLETED = auto()
        CANCELED = auto()
        FAILED = auto()

    _title: str
    _task_fn: Callable[[TaskContext], Awaitable[None]]
    _scheduler: AsyncTaskScheduler | None
    _status: TaskStatus
    _cancel_event: threading.Event
    _state: State
    _error: str | None

    def __init__(self, title: str, task_fn: Callable[..., Awaitable[None]], *args: Any, **kwargs: Any) -> None:
        self._title = title
        self._cancel_event = threading.Event()
        self._status = TaskStatus(cancel_event=self._cancel_event, progress_callback=self._progress_callback)
        self._task_fn = lambda ctx: task_fn(ctx, *args, **kwargs)
        self._scheduler = None
        self._state = self.State.INITIALIZED
        self._error = None

    def _progress_callback(self, status: TaskStatus) -> None:
        if status.is_complete():
            self._state = self.State.COMPLETED

    async def start(self, gui_request_callback: GuiRequestCallback) -> None:
        self._state = self.State.RUNNING
        try:
            self._scheduler = await AsyncTaskScheduler.execute(gui_request_callback, self._task_fn, self._status)
        except Exception as exc:
            self._state = self.State.FAILED
            self._error = str(exc)

    def cancel(self) -> None:
        self._cancel_event.set()

    @property
    def title(self) -> str:
        return self._title

    @property
    def state(self) -> State:
        return self._state

    @property
    def progress(self) -> Progress:
        return self._status.progress

    @property
    def error(self) -> str | None:
        """Exception message if the job failed; None otherwise."""
        return self._error
```

- [ ] **Step 4: Run tests to confirm they pass**

```
uv run pytest tests/scheduler/test_job_failed_state.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Run full QA**

```
uv run qa
```
Expected: zero failures.

- [ ] **Step 6: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All new symbols use correct naming (`snake_case` properties, `UPPER_CASE` constants, `_` prefix for private)
- [ ] Full type annotations on all methods
- [ ] No `Optional[X]` — uses `X | None`
- [ ] QA passes

---

## Task 2: Implement `JobRegistry`

**Files:**
- Create: `src/nova_navigator/dialogs/job_registry.py`
- Test: `tests/dialogs/test_job_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/dialogs/__init__.py` (empty file) and `tests/dialogs/test_job_registry.py`:

```python
import threading
from collections import deque
from unittest.mock import MagicMock

import pytest

from nova_navigator.dialogs.job_registry import MAX_FINISHED_JOBS, JobRegistry
from nova_navigator.scheduler import Job, TaskContext


def _make_job(title: str = "test", state: Job.State = Job.State.RUNNING) -> Job:
    """Create a Job with a mocked internal state for testing."""
    job = MagicMock(spec=Job)
    job.title = title
    job.state = state
    return job


def test_add_job_appears_in_running() -> None:
    registry = JobRegistry()
    job = _make_job()
    registry.add_job(job)
    assert job in registry.running_jobs


def test_update_moves_completed_job_to_finished() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    job.state = Job.State.COMPLETED
    registry.update()
    assert job not in registry.running_jobs
    assert job in registry.finished_jobs


def test_update_moves_failed_job_to_finished() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    job.state = Job.State.FAILED
    registry.update()
    assert job in registry.finished_jobs


def test_update_moves_canceled_job_to_finished() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    job.state = Job.State.CANCELED
    registry.update()
    assert job in registry.finished_jobs


def test_running_job_stays_running_after_update() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    registry.update()
    assert job in registry.running_jobs
    assert job not in registry.finished_jobs


def test_remove_job_removes_from_finished() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    job.state = Job.State.COMPLETED
    registry.update()
    registry.remove_job(job)
    assert job not in registry.finished_jobs


def test_remove_job_noop_if_not_present() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.COMPLETED)
    registry.remove_job(job)  # should not raise


def test_finished_jobs_capped_at_max() -> None:
    registry = JobRegistry()
    jobs = [_make_job(f"job-{i}", state=Job.State.RUNNING) for i in range(MAX_FINISHED_JOBS + 3)]
    for job in jobs:
        registry.add_job(job)
    for job in jobs:
        job.state = Job.State.COMPLETED
    registry.update()
    assert len(registry.finished_jobs) == MAX_FINISHED_JOBS


def test_finished_jobs_newest_first() -> None:
    registry = JobRegistry()
    j1 = _make_job("first", Job.State.RUNNING)
    j2 = _make_job("second", Job.State.RUNNING)
    registry.add_job(j1)
    registry.add_job(j2)
    j1.state = Job.State.COMPLETED
    registry.update()
    j2.state = Job.State.COMPLETED
    registry.update()
    result = registry.finished_jobs
    assert result[0] is j2
    assert result[1] is j1
```

- [ ] **Step 2: Run to confirm tests fail**

```
uv run pytest tests/dialogs/test_job_registry.py -v
```
Expected: FAIL — module does not exist yet.

- [ ] **Step 3: Implement `job_registry.py`**

Create `src/nova_navigator/dialogs/job_registry.py`:

```python
from collections import deque

from nova_navigator.scheduler import Job

MAX_FINISHED_JOBS = 20  # TODO: replace with config value


class JobRegistry:
    """Tracks running and recently-finished jobs.

    Call :meth:`add_job` when a job is started.
    Call :meth:`update` periodically (e.g. on a timer tick) to transition
    jobs that have left the RUNNING state into the finished history.
    Call :meth:`remove_job` when the user dismisses a finished job.
    """

    _running: list[Job]
    _finished: deque[Job]

    def __init__(self) -> None:
        self._running = []
        self._finished = deque()

    def add_job(self, job: Job) -> None:
        """Register *job* as a running job."""
        self._running.append(job)

    def update(self) -> None:
        """Move any non-running jobs from the running list to the finished history."""
        still_running: list[Job] = []
        for job in self._running:
            if job.state == Job.State.RUNNING:
                still_running.append(job)
            else:
                self._finished.appendleft(job)
                while len(self._finished) > MAX_FINISHED_JOBS:
                    self._finished.pop()
        self._running = still_running

    def remove_job(self, job: Job) -> None:
        """Remove *job* from the finished history (user dismissed it)."""
        try:
            self._finished.remove(job)
        except ValueError:
            pass

    @property
    def running_jobs(self) -> list[Job]:
        """Snapshot of currently running jobs."""
        return list(self._running)

    @property
    def finished_jobs(self) -> list[Job]:
        """Snapshot of finished jobs, newest first."""
        return list(self._finished)
```

- [ ] **Step 4: Run tests to confirm they pass**

```
uv run pytest tests/dialogs/test_job_registry.py -v
```
Expected: PASS (9 tests)

- [ ] **Step 5: Run full QA**

```
uv run qa
```
Expected: zero failures.

- [ ] **Step 6: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] `MAX_FINISHED_JOBS` is `UPPER_CASE` constant
- [ ] All methods fully type-annotated
- [ ] Uses `list[Job]` / `deque[Job]` not `List`/`Deque`
- [ ] QA passes

---

## Task 3: Implement `JobsDialog` and `JobRow`

**Files:**
- Create: `src/nova_navigator/dialogs/jobs_dialog.py`

No automated widget tests are written here — the dialog requires a Textual app harness and is exercised by manual testing / QA after wiring in Task 4.

- [ ] **Step 1: Create `src/nova_navigator/dialogs/jobs_dialog.py`**

```python
from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Label, ProgressBar

from nova_navigator.scheduler import Job

from .job_registry import JobRegistry
from ..widgets.overlay_widget import OverlayWidget


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
    }
    JobRow Label {
        width: 1fr;
    }
    JobRow Button {
        width: 3;
        min-width: 3;
        height: 1;
        border: none;
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

        # update progress bars if running
        if job.state == Job.State.RUNNING:
            progress = job.progress
            step_total = max(1, progress.step_total)
            overall_total = max(1, progress.total)
            self._step_bar.update(total=step_total, progress=progress.step_completed)
            self._overall_bar.update(total=overall_total, progress=progress.completed)

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
            self._rows[id(job)].refresh_job(job)

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
```

- [ ] **Step 2: Run lint/type-check to catch errors early**

```
uv run ruff check src/nova_navigator/dialogs/jobs_dialog.py
uv run ty check src/nova_navigator/dialogs/
```
Expected: no errors. Fix any issues before continuing.

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All methods fully type-annotated
- [ ] Private fields prefixed with `_`
- [ ] `snake_case` for all methods/variables, `UpperCamelCase` for classes
- [ ] No `Optional[X]` usage
- [ ] Lint/type-check clean

---

## Task 4: Update `dialogs/__init__.py` and wire into `MainScreen`

**Files:**
- Modify: `src/nova_navigator/dialogs/__init__.py`
- Modify: `src/nova_navigator/main.py`

- [ ] **Step 1: Update `dialogs/__init__.py`**

Replace the contents of `src/nova_navigator/dialogs/__init__.py` with:

```python
from .bookmarks_dialog import BookmarksDialog
from .dialog import DefaultButton
from .files_dialog import CopyMoveFilesDialog, DeleteFilesDialog
from .job_registry import JobRegistry
from .jobs_dialog import JobsDialog

# from .processes_dialog import ProcessesDialog

__all__ = [
    "BookmarksDialog",
    "CopyMoveFilesDialog",
    "DefaultButton",
    "DeleteFilesDialog",
    "JobRegistry",
    "JobsDialog",
    # "ProcessesDialog",
]
```

- [ ] **Step 2: Update `main.py` imports**

In `src/nova_navigator/main.py`, find the existing dialogs import line:

```python
from nova_navigator.dialogs import BookmarksDialog
```

Replace it with:

```python
from nova_navigator.dialogs import BookmarksDialog, JobRegistry, JobsDialog
```

- [ ] **Step 3: Add `_job_registry` and `_jobs_dialog` fields to `MainScreen`**

In `src/nova_navigator/main.py`, find the existing field declarations block:

```python
    _bookmark_dialog: BookmarksDialog
```

Replace with:

```python
    _bookmark_dialog: BookmarksDialog
    _job_registry: JobRegistry
    _jobs_dialog: JobsDialog
```

- [ ] **Step 4: Initialise registry and mount dialog in `__init__` and `compose`**

In `MainScreen.__init__`, find:

```python
    def __init__(self) -> None:
        super().__init__()
        self._terminal_mode = self._TerminalMode.MINIMIZED
        # self._operations = []
```

Replace with:

```python
    def __init__(self) -> None:
        super().__init__()
        self._terminal_mode = self._TerminalMode.MINIMIZED
        self._job_registry = JobRegistry()
        # self._operations = []
```

In `MainScreen.compose`, find the line that yields `Footer()`:

```python
        yield Footer()
```

Replace with:

```python
        self._jobs_dialog = JobsDialog(position=(4, 2), registry=self._job_registry)
        yield self._jobs_dialog
        yield Footer()
```

- [ ] **Step 5: Implement `action_show_processes`**

In `src/nova_navigator/main.py`, find the commented-out block:

```python
    # async def _action_show_processes(self) -> None:
    #     processes_dialog = ProcessesDialog(position=(4, 4), operations=self._operations)
    #     await self.mount(processes_dialog)
    #     processes_dialog.focus()
```

Replace with:

```python
    def action_show_processes(self) -> None:
        self._jobs_dialog.show()
        self._jobs_dialog.focus()
```

- [ ] **Step 6: Wire `add_job` in `action_copy_or_move_files`**

Find the method `action_copy_or_move_files` in `main.py`:

```python
    @work
    async def action_copy_or_move_files(self, move: bool) -> None:
        source_paths = list(self.active_panel().selected_path_items)
        destination_path = self.other_panel().path

        job = await copy_or_move_files_job(
            src_paths=source_paths,
            dst_path=destination_path,
            move=move,
        )
        if job is not None:
            await job.start(self.request_callback)
```

Replace with:

```python
    @work
    async def action_copy_or_move_files(self, move: bool) -> None:
        source_paths = list(self.active_panel().selected_path_items)
        destination_path = self.other_panel().path

        job = await copy_or_move_files_job(
            src_paths=source_paths,
            dst_path=destination_path,
            move=move,
        )
        if job is not None:
            self._job_registry.add_job(job)
            await job.start(self.request_callback)
```

- [ ] **Step 7: Wire `add_job` in `action_delete_files`**

Find:

```python
    @work
    async def action_delete_files(self) -> None:
        paths = list(self.active_panel().selected_path_items)

        job = await delete_files_job(
            paths=paths,
        )
        if job is not None:
            await job.start(self.request_callback)
```

Replace with:

```python
    @work
    async def action_delete_files(self) -> None:
        paths = list(self.active_panel().selected_path_items)

        job = await delete_files_job(
            paths=paths,
        )
        if job is not None:
            self._job_registry.add_job(job)
            await job.start(self.request_callback)
```

- [ ] **Step 8: Run full QA**

```
uv run qa
```
Expected: zero failures.

- [ ] **Step 9: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All new/edited methods fully type-annotated
- [ ] QA passes

---

## Final Verification

- [ ] Run the app and open the jobs dialog with `Ctrl+K` — verify "No jobs" label appears
- [ ] Trigger a file copy (F5) and open the dialog — verify the job appears with two progress bars and a `✕` cancel button
- [ ] Wait for it to complete — verify the row turns green and shows `✓`
- [ ] Click `✓` — verify the row disappears
- [ ] Run full QA one final time: `uv run qa`
