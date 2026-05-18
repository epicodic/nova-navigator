import threading
from unittest.mock import AsyncMock

from nova_navigator.scheduler.context import Progress, TaskContext, TaskStatus, _SubtaskTracker


def test_progress_current_item_default() -> None:
    p = Progress()
    assert p.current_item == ""


def test_set_current_item_updates_progress() -> None:
    calls: list[str] = []

    def cb(status: TaskStatus) -> None:
        calls.append(status.progress.current_item)

    status = TaskStatus(cancel_event=threading.Event(), progress_callback=cb)
    status.set_current_item("file.txt")
    assert status.progress.current_item == "file.txt"
    assert calls == ["file.txt"]


def test_task_context_set_current_item_delegates() -> None:
    calls: list[str] = []

    def cb(s: TaskStatus) -> None:
        calls.append(s.progress.current_item)

    status = TaskStatus(cancel_event=threading.Event(), progress_callback=cb)
    ctx = TaskContext(
        _status=status,
        _response_requester=AsyncMock(),
        _subtask_tracker=_SubtaskTracker(),
    )
    ctx.set_current_item("delegated.txt")
    assert status.progress.current_item == "delegated.txt"
    assert calls == ["delegated.txt"]


# ── Progress.effective_completed ──────────────────────────────────────────────


def test_effective_completed_all_zero() -> None:
    # No progress at all — should return 0.0 without dividing by zero.
    p = Progress()
    assert p.effective_completed == 0.0


def test_effective_completed_no_step_progress() -> None:
    # Only whole-item progress, no step counters set — step_total is 0, so
    # the step fraction must not blow up (denominator clamped to 1).
    p = Progress(completed=3, total=10)
    assert p.effective_completed == 3.0


def test_effective_completed_half_step() -> None:
    # 2 whole items done, current step is 50 % through.
    p = Progress(completed=2, total=10, step_total=100, step_completed=50)
    assert p.effective_completed == 2.5


def test_effective_completed_step_total_zero_no_division_error() -> None:
    # step_total == 0 but step_completed > 0 — denominator clamped to 1,
    # then the result is clamped to total.
    p = Progress(completed=1, total=5, step_total=0, step_completed=99)
    assert p.effective_completed == 5.0  # min(1 + 99/1, 5)


def test_effective_completed_step_fully_complete() -> None:
    # Current step at 100 % adds exactly 1.0 to completed.
    p = Progress(completed=4, total=10, step_total=200, step_completed=200)
    assert p.effective_completed == 5.0


def test_effective_completed_all_complete() -> None:
    # completed == total, step also done — result is clamped to total.
    p = Progress(completed=10, total=10, step_total=50, step_completed=50)
    assert p.effective_completed == 10.0
