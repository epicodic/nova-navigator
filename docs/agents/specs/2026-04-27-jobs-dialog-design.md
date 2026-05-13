# Jobs Dialog — Design Spec

Date: 2026-04-27

## Overview

Implement a `JobsDialog` that gives the user live insight into running, completed, and failed jobs in Nova Navigator.
The dialog floats above the rest of the GUI as a persistent `OverlayWidget`, toggled by `Ctrl+K`.
Job state is managed by a new `JobRegistry` class that lives on `MainScreen`.

---

## Decisions Summary

| Topic | Decision |
|---|---|
| Job failure state | Add `FAILED` as a distinct `Job.State` |
| Progress bridge | Poll with `set_interval` (~0.5 s) |
| Job registry | `JobRegistry` plain class on `MainScreen` |
| Finished job retention | Combined cap, constant `MAX_FINISHED_JOBS = 20` |
| Registry state transitions | `registry.update()` called on each timer tick |
| Progress bar order | Step (top), overall (bottom) |
| Action buttons | Small icon-only buttons (`✕` cancel, `✓`/`✗` remove) |

---

## 1. Data Layer

### 1.1 `Job.State` — add `FAILED`

Add `FAILED` to the existing `Job.State` enum in `src/nova_navigator/scheduler/job.py`.

```python
class State(Enum):
    INITIALIZED = auto()
    RUNNING = auto()
    COMPLETED = auto()
    CANCELED = auto()
    FAILED = auto()
```

Wrap the `await AsyncTaskScheduler.execute(...)` call in `Job.start()` with a try/except.
On any unhandled exception, set `self._state = Job.State.FAILED` and store `self._error: str` with the exception message.
The exception is **not** re-raised — callers of `job.start()` do not need to change.

Add a read-only property `error: str | None` to `Job` (returns `None` unless state is `FAILED`).

### 1.2 `JobRegistry`

New file: `src/nova_navigator/dialogs/job_registry.py`.

```python
MAX_FINISHED_JOBS = 20  # replace with config value later
```

```
JobRegistry
  _running: list[Job]
  _finished: deque[Job]   # maxlen = MAX_FINISHED_JOBS; oldest dropped when full

  add_job(job: Job) -> None
      Appends job to _running.

  update() -> None
      Scans _running; moves any job whose state is not RUNNING to _finished.
      Oldest finished jobs are dropped when the deque is at capacity.

  remove_job(job: Job) -> None
      Removes job from _finished (user dismissed it).

  running_jobs -> list[Job]    # snapshot copy
  finished_jobs -> list[Job]   # newest-first snapshot copy
```

`update()` is O(n) on the running list, which is almost always tiny.
`remove_job()` is a no-op if the job is not in `_finished`.

### 1.3 `MainScreen` changes

`MainScreen` adds a `_job_registry: JobRegistry` field.
Before calling `job.start()`, it calls `self._job_registry.add_job(job)`.
No call is needed after `job.start()` returns — `registry.update()` handles the transition.

The `JobsDialog` is mounted once in `compose()` (hidden initially) and stored as `self._jobs_dialog`.
`action_show_processes` shows it; `Ctrl+K` binding is already present.

---

## 2. UI Layer

### 2.1 File

`src/nova_navigator/dialogs/jobs_dialog.py`

### 2.2 `JobsDialog` (extends `OverlayWidget`)

- Mounted once at startup; `close_action = CloseAction.HIDE`.
- `close_on_blur = False` — the dialog stays visible while the user clicks buttons or navigates panels.
- Fixed width (~60 chars); height is auto/scrollable.
- A `set_interval(0.5, self._tick)` timer drives all updates.
- On each `_tick`: call `registry.update()`, then reconcile the widget list.

### 2.3 Widget tree

```
JobsDialog (OverlayWidget)
  VerticalScroll
    JobRow (one per job, keyed by job identity)
      Horizontal
        Label (job title)
        Button (icon-only, width=3)
      ProgressBar  ← step progress (top)
      ProgressBar  ← overall progress (bottom)
      [progress bars absent for finished jobs]
```

### 2.4 `JobRow`

A custom `Widget` subclass.
Constructor takes `job: Job` and a callback `on_action: Callable[[Job], None]`.
`on_action` is called when the user clicks the button; `JobsDialog` handles the callback:
- If the job is `RUNNING`, calls `job.cancel()`.
- Otherwise, calls `registry.remove_job(job)` and removes the `JobRow` from the DOM.

Exposes `refresh_job(job: Job) -> None` for in-place update of progress bars and button state without remounting.

Button icons and semantics:

| Job state | Icon | Action |
|---|---|---|
| `RUNNING` | `✕` | Cancel the job (`job.cancel()`) |
| `COMPLETED` | `✓` | Remove from registry |
| `CANCELED` | `✗` | Remove from registry |
| `FAILED` | `✗` | Remove from registry |

### 2.5 Reconciliation on each tick

The dialog maintains a `dict[int, JobRow]` keyed by `id(job)`.
On each tick:
1. Call `registry.update()`.
2. Build the new desired job list: `running_jobs + finished_jobs`.
3. Mount `JobRow` for any job not yet in the dict.
4. Call `row.refresh_job(job)` for jobs already in the dict.
5. Remove `JobRow` widgets for jobs no longer in either list (dropped from history).

### 2.6 Empty state

When both lists are empty, display a single centered `Label("No jobs")`.
Remove it when the first job appears.

### 2.7 Styling

```css
JobRow.-running  { background: $panel; }
JobRow.-completed { background: $success 20%; }
JobRow.-canceled  { background: $error 15%; }
JobRow.-failed    { background: $error 20%; }

JobRow Button { width: 3; min-width: 3; }
```

CSS classes `.-running`, `.-completed`, `.-canceled`, `.-failed` are applied in `refresh_job`.

### 2.8 Progress bars

Textual's built-in `ProgressBar` widget is used.
Total is passed as `max(1, progress.total)` and `max(1, progress.step_total)` to avoid division-by-zero.
Progress bars are only shown (and mounted) for `RUNNING` jobs.
They are removed when the job leaves the running state.

---

## 3. Error Handling & Edge Cases

| Case | Handling |
|---|---|
| Task raises unhandled exception | `Job.start()` catches it → `state = FAILED`, `error` stores message |
| `CANCELED` display | Red-tinted row; title shows `" (canceled)"` suffix |
| `FAILED` display | Red-tinted row; title shows `" (failed)"` suffix; `error` shown as sub-label if non-empty |
| `progress.total == 0` | Pass `max(1, total)` to `ProgressBar` |
| Job finishes between ticks | Caught on next `registry.update()` tick; no special case needed |
| Dialog hidden while jobs run | Timer still ticks; `registry.update()` keeps state current |
| History cap exceeded | Oldest finished job silently dropped from `_finished` deque |

---

## 4. Files Changed / Created

| File | Change |
|---|---|
| `src/nova_navigator/scheduler/job.py` | Add `FAILED` state; wrap `start()` with try/except; add `error` property |
| `src/nova_navigator/dialogs/job_registry.py` | New — `JobRegistry` class + `MAX_FINISHED_JOBS` constant |
| `src/nova_navigator/dialogs/jobs_dialog.py` | New — `JobsDialog` + `JobRow` widgets |
| `src/nova_navigator/dialogs/__init__.py` | Export `JobsDialog`, `JobRegistry` |
| `src/nova_navigator/main.py` | Add `_job_registry`, mount `JobsDialog`, wire `add_job()` calls, implement `action_show_processes` |

---

## 5. Out of Scope

- Config-driven `MAX_FINISHED_JOBS` (replace constant later)
- Auto-clearing completed jobs after a delay
- Error details dialog (clicking on a failed job)
- Jobs from sources other than `MainScreen`
